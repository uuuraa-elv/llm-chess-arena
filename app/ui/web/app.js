/* UI controller LLM Chess Arena. Menghubungkan QWebChannel <-> tampilan. */
(function () {
  "use strict";

  var bridge = null;
  var board = null;
  var hasKey = false;
  var matchRunning = false;
  var matchPaused = false;
  var pendingResume = false;   // ada pertandingan tersimpan & model ter-preselect

  var selected = { a: null, b: null };  // { id, name }
  var models = [];
  var firstLoad = true;   // auto-preselect hanya saat pemuatan pertama
  var apiCalls = 0;
  var lastRow = null;     // baris movelist berjalan
  var lastSanEl = null;

  // Antrean render papan: tunggu animasi selesai sebelum langkah berikutnya
  // (sinkron animasi <-> giliran <-> suara). Worker Python tetap non-blocking;
  // hanya update visual yang di-serialkan di sini.
  var boardQueue = [];
  var animating = false;

  // Penalaran AI (Fitur 1)
  var reasoningLog = [];   // {game, fullmove, color, model, san, uci, reasoning, eval_rounds, thinking_ms}
  var currentGame = 1;
  var allCollapsed = false;
  var THINK_DOM_CAP = 400; // batasi node DOM panel penalaran (export tetap lengkap di reasoningLog)

  // State "sedang berpikir" yang ditunda agar highlight sinkron dgn papan (antrean).
  var pendingThinkState = null;

  // Efek suara (opsional, default mati)
  var soundOn = false;
  var audioCtx = null;

  /* ----------------------------- util ----------------------------- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function show(node) { node.classList.remove("hidden"); }
  function hide(node) { node.classList.add("hidden"); }
  function empty(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  /* --------------------------- theme ------------------------------ */
  function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem("arena-theme"); } catch (e) {}
    if (saved) document.documentElement.setAttribute("data-theme", saved);
    $("#theme-toggle").addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", cur);
      try { localStorage.setItem("arena-theme", cur); } catch (e) {}
    });
  }

  /* --------------------------- logging ---------------------------- */
  var VALID_LEVELS = { info: 1, warn: 1, error: 1, illegal: 1 };
  function addLog(level, message) {
    var safe = VALID_LEVELS[level] ? level : "info";  // cegah injeksi class CSS
    var box = $("#log");
    var line = el("div", "log-line log-" + safe);
    line.appendChild(el("span", "tag", safe === "illegal" ? "ilegal" : safe));
    line.appendChild(el("span", "msg", message));
    box.appendChild(line);
    while (box.children.length > 300) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
  }

  /* -------------------------- combobox ---------------------------- */
  function setupCombo(side) {
    var wrap = $('.combo[data-side="' + side + '"]');
    var input = $(".combo-input", wrap);
    var list = $(".combo-list", wrap);

    function render(filter) {
      empty(list);
      var q = (filter || "").toLowerCase();
      var matched = models.filter(function (m) {
        return m.id.toLowerCase().indexOf(q) >= 0 || (m.name || "").toLowerCase().indexOf(q) >= 0;
      }).slice(0, 50);
      if (!matched.length) {
        list.appendChild(el("li", "empty", "Tidak ada model cocok"));
        return;
      }
      matched.forEach(function (m) {
        var li = el("li");
        li.setAttribute("role", "option");
        li.appendChild(el("span", null, m.name || m.id));
        li.appendChild(el("small", null, m.id));
        li.addEventListener("mousedown", function (ev) {
          ev.preventDefault();
          pick(side, m, input);
          onUserPickedModel();   // user pilih manual -> ini pertandingan baru, bukan resume
          list.classList.remove("open");
        });
        list.appendChild(li);
      });
    }

    // Saat fokus: tampilkan SELURUH daftar (mudah di-browse) & select teks lama
    // supaya ketikan pertama langsung mengganti filter.
    input.addEventListener("focus", function () { input.select(); render(""); list.classList.add("open"); });
    input.addEventListener("input", function () { render(input.value); list.classList.add("open"); });
    input.addEventListener("blur", function () { setTimeout(function () { list.classList.remove("open"); }, 120); });
    input.addEventListener("keydown", function (ev) {
      var items = $all("li:not(.empty)", list);
      var active = $(".active", list);
      var idx = items.indexOf(active);
      if (ev.key === "ArrowDown") { ev.preventDefault(); move(items, idx + 1); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); move(items, idx - 1); }
      else if (ev.key === "Enter" && active) { ev.preventDefault(); active.dispatchEvent(new MouseEvent("mousedown")); }
      else if (ev.key === "Escape") { list.classList.remove("open"); }
    });
    function move(items, i) {
      if (!items.length) return;
      i = (i + items.length) % items.length;
      items.forEach(function (it) { it.classList.remove("active"); });
      items[i].classList.add("active");
      items[i].scrollIntoView({ block: "nearest" });
    }
  }

  function pick(side, model, input) {
    selected[side] = { id: model.id, name: model.name || model.id };
    input.value = model.name || model.id;
    var lbl = $('.combo-selected[data-side="' + side + '"]');
    lbl.textContent = model.id;
    lbl.classList.add("set");
    refreshStart();
  }

  function refreshStart() {
    $("#start-btn").disabled = !(selected.a && selected.b);
  }

  function preselect() {
    function find(preds) {
      for (var p = 0; p < preds.length; p++) {
        for (var i = 0; i < models.length; i++) {
          if (preds[p](models[i].id.toLowerCase())) return models[i];
        }
      }
      return null;
    }
    var a = find([function (id) { return id.indexOf("sonnet") >= 0; }]);
    var b = find([
      function (id) { return id.indexOf("gpt-5-mini") >= 0; },
      function (id) { return id.indexOf("gpt-5") >= 0; },
      function (id) { return id.indexOf("gpt-4o-mini") >= 0; },
    ]);
    if (a) pick("a", a, $('.combo[data-side="a"] .combo-input'));
    if (b && (!a || b.id !== a.id)) pick("b", b, $('.combo[data-side="b"] .combo-input'));
  }

  /* --------------------------- models ----------------------------- */
  function setRefreshLoading(on) {
    var btn = $("#refresh-btn");
    if (btn) btn.classList.toggle("loading", on);
  }

  function clearSelection(side) {
    selected[side] = null;
    var inp = $('.combo[data-side="' + side + '"] .combo-input');
    if (inp) inp.value = "";
    var lbl = $('.combo-selected[data-side="' + side + '"]');
    if (lbl) { lbl.textContent = "Belum dipilih"; lbl.classList.remove("set"); }
  }

  function onModels(d) {
    var status = $("#models-status");
    if (d.loading) {
      status.textContent = "Memuat daftar model…";
      status.classList.remove("error");
      setRefreshLoading(true);
      return;
    }
    setRefreshLoading(false);
    if (d.error) {
      status.textContent = "Gagal memuat model: " + d.error;
      status.classList.add("error");
      return;
    }
    models = d.models || [];
    status.classList.remove("error");
    status.textContent = models.length + " model OpenRouter siap dipilih.";
    // Pertahankan pilihan yang masih ada di katalog; kosongkan yang sudah hilang.
    ["a", "b"].forEach(function (side) {
      if (selected[side] && !models.some(function (m) { return m.id === selected[side].id; })) {
        clearSelection(side);
      }
    });
    // Pemuatan pertama: cek pertandingan tersimpan (resume) atau auto-pilih contoh matchup.
    if (firstLoad) { firstLoad = false; refreshResumeBanner(true); }
    refreshStart();
  }

  /* --------------------------- api key ---------------------------- */
  function onApiStatus(d) {
    hasKey = !!d.has_key;
    if (hasKey) hide($("#api-banner")); else show($("#api-banner"));
  }
  function setupApiKey() {
    $("#api-key-save").addEventListener("click", function () {
      var key = $("#api-key-input").value.trim();
      if (!key) { addLog("error", "API key kosong."); return; }
      bridge.save_api_key(key, function (res) {
        var r = JSON.parse(res);
        if (r.ok) {
          $("#api-key-input").value = "";
          hide($("#api-banner"));
          addLog("info", "API key tersimpan. Memuat ulang daftar model…");
          bridge.request_models();
        } else {
          addLog("error", "Gagal simpan key: " + r.error);
        }
      });
    });
  }

  /* --------------------------- settings --------------------------- */
  function openSettings() {
    if (!bridge) return;
    var err = $("#set-error"); hide(err); err.textContent = "";
    var keyInput = $("#set-key");
    keyInput.value = ""; keyInput.type = "password";
    $("#set-key-toggle").textContent = "Lihat";
    bridge.get_settings(function (res) {
      var d = JSON.parse(res);
      $("#set-baseurl").value = d.base_url || "";
      $("#set-baseurl").placeholder = d.default_base_url || "https://openrouter.ai/api/v1";
      keyInput.placeholder = d.has_key ? "•••••• (tersimpan, isi untuk ganti)" : "sk-or-...";
    });
    show($("#settings-overlay"));
  }
  function closeSettings() { hide($("#settings-overlay")); }
  function saveSettings() {
    if (!bridge) return;
    var key = $("#set-key").value.trim();
    var baseUrl = $("#set-baseurl").value.trim();
    bridge.save_settings(key, baseUrl, function (res) {
      var r = JSON.parse(res);
      if (r.ok) {
        closeSettings();
        addLog("info", "Pengaturan disimpan (Base URL: " + r.base_url + "). Memuat ulang model…");
        bridge.request_models();
      } else {
        var err = $("#set-error");
        err.textContent = r.error || "Gagal menyimpan pengaturan.";
        show(err);
      }
    });
  }
  function setupSettings() {
    $("#settings-btn").addEventListener("click", openSettings);
    $("#set-cancel").addEventListener("click", closeSettings);
    $("#set-save").addEventListener("click", saveSettings);
    $("#settings-overlay").addEventListener("click", function (ev) {
      if (ev.target === $("#settings-overlay")) closeSettings();  // klik area luar = tutup
    });
    $("#set-key-toggle").addEventListener("click", function () {
      var inp = $("#set-key");
      var showing = inp.type === "text";
      inp.type = showing ? "password" : "text";
      $("#set-key-toggle").textContent = showing ? "Lihat" : "Sembunyikan";
    });
  }

  /* ----------------------- match lifecycle ------------------------ */
  function startMatch() {
    if (!selected.a || !selected.b) return;
    if (!hasKey) { show($("#api-banner")); addLog("error", "Set API key dulu untuk memulai."); return; }
    apiCalls = 0; lastRow = null; lastSanEl = null;
    boardQueue = []; animating = false;
    matchPaused = false; setPauseBtn(false); enablePause(true);
    empty($("#movelist"));
    empty($("#log"));
    resetThink();
    resetEvalBar();
    hide($("#result-badge"));
    setApiCalls(0);
    if (board) board.reset();

    $("#sb-a .sf-name").textContent = selected.a.name;
    $("#sb-b .sf-name").textContent = selected.b.name;
    $("#sb-a .sf-score").textContent = "0";
    $("#sb-b .sf-score").textContent = "0";

    hide($("#setup")); show($("#arena")); hide($("#winner-overlay")); hide($("#back-btn"));
    matchRunning = true;
    bridge.start_match(selected.a.id, selected.b.id);
  }

  function backToSetup() {
    hide($("#arena")); hide($("#winner-overlay")); show($("#setup"));
    refreshResumeBanner(false);
  }

  /* ---------------------------- resume ---------------------------- */
  function onUserPickedModel() {
    // User memilih model manual -> ini pertandingan baru, bukan melanjutkan simpanan.
    pendingResume = false;
    hide($("#resume-banner"));
  }
  function preselectFromSaved(idA, idB) {
    var ma = models.filter(function (m) { return m.id === idA; })[0];
    var mb = models.filter(function (m) { return m.id === idB; })[0];
    if (ma) pick("a", ma, $('.combo[data-side="a"] .combo-input'));
    if (mb) pick("b", mb, $('.combo[data-side="b"] .combo-input'));
  }
  function refreshResumeBanner(allowPreselect) {
    if (!bridge || !bridge.get_saved_session) return;
    bridge.get_saved_session(function (res) {
      var s;
      try { s = JSON.parse(res); } catch (e) { s = { has: false }; }
      var inCatalog = s.has &&
        models.some(function (m) { return m.id === s.model_a; }) &&
        models.some(function (m) { return m.id === s.model_b; });
      if (inCatalog) {
        pendingResume = true;
        var t = $("#resume-text");
        empty(t);
        t.appendChild(el("span", null, "Pertandingan tersimpan: "));
        t.appendChild(el("strong", null, s.model_a + " vs " + s.model_b));
        t.appendChild(el("span", null,
          " (skor " + s.score_a + "-" + s.score_b + ", game " + s.game_index +
          "). Klik Mulai untuk melanjutkan."));
        show($("#resume-banner"));
        preselectFromSaved(s.model_a, s.model_b);
      } else {
        pendingResume = false;
        hide($("#resume-banner"));
        if (allowPreselect && !selected.a && !selected.b) preselect();
      }
    });
  }
  function discardSavedSession() {
    if (bridge && bridge.clear_saved_session) bridge.clear_saved_session();
    pendingResume = false;
    hide($("#resume-banner"));
    clearSelection("a"); clearSelection("b");
    refreshStart();
  }
  function onStart() {
    if (pendingResume) resumeMatch();
    else startMatch();
  }
  function resumeMatch() {
    if (!hasKey) { show($("#api-banner")); addLog("error", "Set API key dulu untuk melanjutkan."); return; }
    apiCalls = 0; lastRow = null; lastSanEl = null;
    boardQueue = []; animating = false;
    matchPaused = false; setPauseBtn(false); enablePause(true);
    empty($("#movelist"));
    empty($("#log"));
    resetThink();
    resetEvalBar();
    hide($("#result-badge"));
    setApiCalls(0);
    if (board) board.reset();
    hide($("#setup")); show($("#arena")); hide($("#winner-overlay")); hide($("#back-btn"));
    hide($("#resume-banner"));
    matchRunning = true;
    pendingResume = false;
    bridge.resume_saved_match();   // engine emit board reset(resumed) -> restorePanels()
  }
  function restorePanels(d) {
    currentGame = d.game_index || 1;
    $("#sb-a .sf-name").textContent = d.model_a || "";
    $("#sb-b .sf-name").textContent = d.model_b || "";
    $("#sb-a .sf-score").textContent = d.score_a || 0;
    $("#sb-b .sf-score").textContent = d.score_b || 0;
    $(".score-game").textContent = "Game " + currentGame;
    if (typeof d.white_is_a === "boolean") {
      $("#sb-a .sf-color").dataset.color = d.white_is_a ? "White" : "Black";
      $("#sb-b .sf-color").dataset.color = d.white_is_a ? "Black" : "White";
    }
    var log = d.restore_log || [];
    log.forEach(function (m) { appendMove(m); appendReasoning(m); });
    setApiCalls(d.api_calls || 0);
    addLog("info", "Pertandingan dilanjutkan dari simpanan (" + log.length + " langkah).");
  }

  /* --------------------------- signals ---------------------------- */
  function setApiCalls(n) {
    if (typeof n !== "number") return;
    apiCalls = n;
    $("#api-calls").textContent = "Panggilan API: " + n;
  }

  function onBoard(d) {
    if (!board) return;
    if (d.reset) {
      // Game baru / resume: buang antrean langkah sebelumnya & reset segera.
      boardQueue = [];
      animating = false;
      pendingThinkState = null;
      board.render(d.fen, null, { reset: true });
      updateEvalBar(d.fen);
      // Lepas highlight "latest" dari langkah terakhir game sebelumnya.
      if (lastSanEl) lastSanEl.classList.remove("latest");
      lastRow = null; lastSanEl = null;
      if (d.resumed) {
        restorePanels(d);   // isi ulang movelist + penalaran dari simpanan
      } else {
        addGameSeparator(d.game_index);
        addThinkSeparator(d.game_index);
      }
      return;
    }
    boardQueue.push(d);
    pumpQueue();
  }

  /* Proses satu langkah dari antrean; tunggu animasi selesai sebelum lanjut.
     Dibungkus guard agar antrean tak pernah macet bila render/append gagal
     (kalau macet, papan akan membeku). `settled` cegah lanjut ganda. */
  function pumpQueue() {
    if (animating || !boardQueue.length) return;
    var d = boardQueue.shift();
    animating = true;
    var settled = false;
    function finish(moveType) {
      if (settled) return;
      settled = true;
      try { playSound(moveType, d); } catch (e) {}   // suara sinkron akhir animasi
      animating = false;
      pumpQueue();
      // Papan sudah menyusul: terapkan highlight "berpikir" yang ditunda agar sinkron.
      if (!animating && !boardQueue.length && pendingThinkState) {
        var ps = pendingThinkState;
        pendingThinkState = null;
        applyThinking(ps);
      }
    }
    try {
      setApiCalls(d.api_calls);
      appendMove(d);
      appendReasoning(d);
      updateEvalBar(d.fen);
      board.render(d.fen, d.last_move, { check: d.check, onDone: finish });
    } catch (e) {
      finish("move");
    }
  }

  function addGameSeparator(gameIndex) {
    var sep = el("div", "game-sep", "— Game " + gameIndex + " —");
    $("#movelist").appendChild(sep);
  }

  function appendMove(d) {
    var list = $("#movelist");
    if (lastSanEl) lastSanEl.classList.remove("latest");
    // SAN dari python-chess (board.san) sudah memuat anotasi + / # — jangan tambah lagi.
    var sanText = d.san || "";
    if (d.mover === "White") {
      lastRow = el("div", "move-row");
      lastRow.appendChild(el("span", "num", d.fullmove + "."));
      var w = el("span", "san w latest", sanText);
      lastRow.appendChild(w);
      lastRow.appendChild(el("span", "san b", ""));
      list.appendChild(lastRow);
      lastSanEl = w;
    } else {
      if (!lastRow) {  // game dimulai dari hitam (jarang) — buat baris isi nomor
        lastRow = el("div", "move-row");
        lastRow.appendChild(el("span", "num", d.fullmove + "."));
        lastRow.appendChild(el("span", "san w", "…"));
        lastRow.appendChild(el("span", "san b", ""));
        list.appendChild(lastRow);
      }
      var bcell = lastRow.children[2];
      bcell.textContent = sanText;
      bcell.classList.add("latest");
      lastSanEl = bcell;
    }
    if (lastSanEl && typeof d.eval_rounds === "number") {
      lastSanEl.title = "AI self-eval: " + d.eval_rounds + "x";
    }
    list.scrollTop = list.scrollHeight;
  }

  /* ----------------------- reasoning panel ------------------------ */
  function fmtTime(ms) {
    if (typeof ms !== "number" || ms < 0) return "";
    if (ms < 1000) return ms + "ms";
    return (ms / 1000).toFixed(1) + "s";
  }
  function updateThinkCount() {
    var c = $("#think-count");
    if (c) c.textContent = reasoningLog.length;
  }
  function resetThink() {
    reasoningLog = [];
    currentGame = 1;
    allCollapsed = false;
    var lw = $("#thinklist");
    empty(lw);
    lw.appendChild(el("div", "think-empty",
      "Penalaran tiap langkah akan muncul di sini saat pertandingan berjalan."));
    updateThinkCount();
    var cb = $("#think-collapse");
    if (cb) cb.textContent = "⊟";
  }
  function addThinkSeparator(gameIndex) {
    currentGame = gameIndex || currentGame;
    var lw = $("#thinklist");
    var emptyEl = $(".think-empty", lw);
    if (emptyEl) lw.removeChild(emptyEl);
    if (lw.children.length) {
      lw.appendChild(el("div", "think-sep", "— Game " + currentGame + " —"));
    }
  }
  function appendReasoning(d) {
    var lw = $("#thinklist");
    var emptyEl = $(".think-empty", lw);
    if (emptyEl) lw.removeChild(emptyEl);

    var model = d.model || "model";
    var reasoning = d.reasoning || "";
    reasoningLog.push({
      game: currentGame,
      fullmove: d.fullmove,
      color: d.mover,
      model: model,
      san: d.san || "",
      uci: d.last_move || "",
      reasoning: reasoning,
      eval_rounds: typeof d.eval_rounds === "number" ? d.eval_rounds : 0,
      thinking_ms: typeof d.thinking_ms === "number" ? d.thinking_ms : -1,
    });

    var prevLatest = $(".think-entry.latest", lw);
    if (prevLatest) prevLatest.classList.remove("latest");

    var sideClass = d.mover_is_a === true ? " side-a"
      : (d.mover_is_a === false ? " side-b" : "");
    var entry = el("div", "think-entry latest" + sideClass);
    entry.setAttribute("data-collapsed", allCollapsed ? "true" : "false");

    var head = el("button", "think-head");
    head.type = "button";
    head.setAttribute("aria-expanded", allCollapsed ? "false" : "true");
    var numLabel = d.fullmove != null ? d.fullmove + (d.mover === "White" ? "." : "…") : "";
    head.appendChild(el("span", "th-no", numLabel));
    var dot = el("span", "th-color");
    if (d.mover) dot.dataset.color = d.mover;
    head.appendChild(dot);
    head.appendChild(el("span", "th-model", model));
    if (d.san) head.appendChild(el("span", "th-move", d.san));
    var t = fmtTime(d.thinking_ms);
    if (t) head.appendChild(el("span", "th-time", t));
    if (d.eval_rounds && d.eval_rounds > 0) {
      var ev = el("span", "th-eval", "×" + d.eval_rounds);
      ev.title = "Putaran evaluasi-diri AI";
      head.appendChild(ev);
    }
    head.appendChild(el("span", "th-caret", "▾"));

    var body = el("div", "think-body");
    body.setAttribute("aria-hidden", allCollapsed ? "true" : "false");
    var inner = el("div", "think-inner");
    var hasReason = !!(reasoning && reasoning.trim());
    inner.appendChild(el("p", "think-text" + (hasReason ? "" : " empty"),
      hasReason ? reasoning : "Tidak ada alasan tercatat."));
    body.appendChild(inner);

    // Klik di-tangani lewat event delegation pada #thinklist (lihat setupThink),
    // jadi tak ada listener per-entry yang menumpuk sepanjang sesi.
    entry.appendChild(head);
    entry.appendChild(body);

    // Auto-scroll hanya jika user sedang berada dekat dasar (jangan rebut scroll).
    var nearBottom = lw.scrollHeight - lw.scrollTop - lw.clientHeight < 70;
    lw.appendChild(entry);
    // Batasi jumlah node DOM (export tetap lengkap di reasoningLog).
    while (lw.children.length > THINK_DOM_CAP) lw.removeChild(lw.firstChild);
    updateThinkCount();
    if (nearBottom) lw.scrollTop = lw.scrollHeight;
  }

  function setEntryCollapsed(entry, collapsed) {
    entry.setAttribute("data-collapsed", collapsed ? "true" : "false");
    var head = $(".think-head", entry);
    if (head) head.setAttribute("aria-expanded", collapsed ? "false" : "true");
    var body = $(".think-body", entry);
    if (body) body.setAttribute("aria-hidden", collapsed ? "true" : "false");
  }
  function setupThink() {
    // Event delegation: satu listener untuk semua kartu (tidak menumpuk).
    $("#thinklist").addEventListener("click", function (ev) {
      var head = ev.target.closest ? ev.target.closest(".think-head") : null;
      if (!head) return;
      var entry = head.parentNode;
      var collapsed = entry.getAttribute("data-collapsed") === "true";
      setEntryCollapsed(entry, !collapsed);
    });
  }
  function toggleCollapseAll() {
    allCollapsed = !allCollapsed;
    $all(".think-entry", $("#thinklist")).forEach(function (en) {
      setEntryCollapsed(en, allCollapsed);
    });
    var btn = $("#think-collapse");
    btn.textContent = allCollapsed ? "⊞" : "⊟";
    btn.setAttribute("aria-pressed", allCollapsed ? "true" : "false");
    btn.setAttribute("aria-label", allCollapsed ? "Buka semua penalaran" : "Lipat semua penalaran");
  }

  /* --------------------------- export ----------------------------- */
  function sanitizeName(s) {
    return (s || "model").replace(/[^a-z0-9._-]+/gi, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "model";
  }
  function triggerDownload(blob, fname) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = fname;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      if (a.parentNode) a.parentNode.removeChild(a);
      URL.revokeObjectURL(url);
    }, 1500);
  }
  function exportReasoning(fmt) {
    if (!reasoningLog.length) { addLog("warn", "Belum ada penalaran untuk diekspor."); return; }
    var aId = selected.a ? selected.a.id : "A";
    var bId = selected.b ? selected.b.id : "B";
    var base = "reasoning-" + sanitizeName(aId) + "-vs-" + sanitizeName(bId);
    var blob, fname;
    if (fmt === "json") {
      blob = new Blob(
        [JSON.stringify({ matchup: { a: aId, b: bId }, moves: reasoningLog }, null, 2)],
        { type: "application/json" });
      fname = base + ".json";
    } else {
      var lines = ["LLM Chess Arena — Reasoning Log", aId + "  vs  " + bId, ""];
      var lastGame = null;
      reasoningLog.forEach(function (m) {
        if (m.game !== lastGame) { lastGame = m.game; lines.push("===== Game " + m.game + " ====="); }
        var num = (m.fullmove != null ? m.fullmove : "") + (m.color === "White" ? "." : "...");
        lines.push(num + " " + m.color + " [" + m.model + "] " + m.san + " (" + m.uci + ")");
        lines.push("   waktu: " + (fmtTime(m.thinking_ms) || "-") +
          (m.eval_rounds ? ", self-eval ×" + m.eval_rounds : ""));
        lines.push("   alasan: " + (m.reasoning && m.reasoning.trim() ? m.reasoning : "(tidak ada)"));
        lines.push("");
      });
      blob = new Blob([lines.join("\n")], { type: "text/plain" });
      fname = base + ".txt";
    }
    triggerDownload(blob, fname);
    addLog("info", "Ekspor penalaran: " + fname);
  }

  /* ---------------------------- sound ----------------------------- */
  function initSound() {
    try { soundOn = localStorage.getItem("arena-sound") === "on"; } catch (e) {}
    updateSoundBtn();
    $("#sound-toggle").addEventListener("click", function () {
      soundOn = !soundOn;
      try { localStorage.setItem("arena-sound", soundOn ? "on" : "off"); } catch (e) {}
      if (soundOn) ensureAudio();   // gesture user -> boleh buat/resume AudioContext
      updateSoundBtn();
    });
  }
  function updateSoundBtn() {
    var btn = $("#sound-toggle");
    if (!btn) return;
    btn.classList.toggle("on", soundOn);
    btn.textContent = soundOn ? "🔊" : "🔇";
    btn.setAttribute("aria-pressed", soundOn ? "true" : "false");
    btn.title = "Efek suara: " + (soundOn ? "nyala" : "mati");
  }
  function ensureAudio() {
    try {
      if (!audioCtx) {
        var AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return null;
        audioCtx = new AC();
      }
      if (audioCtx.state === "suspended") audioCtx.resume();
    } catch (e) { audioCtx = null; }
    return audioCtx;
  }
  function tone(freq, dur, type, gainPeak, delay) {
    var ctx = audioCtx;
    if (!ctx) return;
    try {
      var t0 = ctx.currentTime + (delay || 0);
      var osc = ctx.createOscillator();
      var g = ctx.createGain();
      osc.type = type || "triangle";
      osc.frequency.value = freq;
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(gainPeak || 0.07, t0 + 0.008);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
      osc.connect(g);
      g.connect(ctx.destination);
      osc.start(t0);
      osc.stop(t0 + dur + 0.02);
    } catch (e) {}
  }
  function playSound(moveType, d) {
    if (!soundOn || !ensureAudio()) return;
    if (d && d.checkmate) { tone(660, 0.12, "triangle", 0.09); tone(880, 0.20, "triangle", 0.08, 0.10); return; }
    if (d && d.check) { tone(720, 0.10, "sine", 0.07); return; }
    if (moveType === "capture") { tone(150, 0.10, "sawtooth", 0.07); tone(90, 0.08, "square", 0.05, 0.01); return; }
    if (moveType === "castle") { tone(200, 0.07, "triangle", 0.06); tone(150, 0.07, "triangle", 0.06, 0.06); return; }
    if (moveType === "promotion") { tone(523, 0.09, "triangle", 0.07); tone(784, 0.13, "triangle", 0.07, 0.07); return; }
    tone(180, 0.06, "triangle", 0.06);   // langkah biasa "tock"
  }

  /* ---------------------------- pause ----------------------------- */
  function setPauseBtn(paused) {
    var b = $("#pause-btn");
    if (!b) return;
    b.textContent = paused ? "Lanjutkan" : "Jeda";
    b.classList.toggle("resumed", paused);
  }
  function enablePause(on) {
    var b = $("#pause-btn");
    if (b) b.disabled = !on;
  }
  function togglePause() {
    if (!bridge || !matchRunning) return;
    matchPaused = !matchPaused;
    if (matchPaused) bridge.pause_match(); else bridge.resume_match();
    setPauseBtn(matchPaused);
  }

  /* --------------------------- eval bar --------------------------- */
  // Prakiraan material: Ratu 9, Benteng 5, Peluncur/Kuda 3, Pion 1 (Raja tak dihitung).
  function pieceMaterial(fen) {
    var vals = { P: 1, N: 3, B: 3, R: 5, Q: 9 }, w = 0, b = 0;
    var placement = (fen || "").split(" ")[0];
    for (var i = 0; i < placement.length; i++) {
      var ch = placement[i], up = ch.toUpperCase();
      if (vals[up]) { if (ch === up) w += vals[up]; else b += vals[up]; }
    }
    return { w: w, b: b };
  }
  function updateEvalBar(fen) {
    var m = pieceMaterial(fen), total = m.w + m.b || 1;
    var wPct = Math.max(5, Math.min(95, Math.round((m.w / total) * 100)));
    var white = $(".eb-white"), black = $(".eb-black");
    if (white) white.style.width = wPct + "%";
    if (black) black.style.width = (100 - wPct) + "%";
    var diff = m.w - m.b, e = $("#eb-diff");
    if (!e) return;
    if (diff > 0) { e.textContent = "+" + diff + " W"; e.className = "eb-diff lead-w"; }
    else if (diff < 0) { e.textContent = "+" + (-diff) + " B"; e.className = "eb-diff lead-b"; }
    else { e.textContent = "="; e.className = "eb-diff"; }
  }
  function resetEvalBar() {
    var white = $(".eb-white"), black = $(".eb-black"), e = $("#eb-diff");
    if (white) white.style.width = "50%";
    if (black) black.style.width = "50%";
    if (e) { e.textContent = "="; e.className = "eb-diff"; }
  }

  // Bagian state yang selalu aman diterapkan langsung (nama, skor, game, warna).
  function applyStateBase(d) {
    $("#sb-a .sf-name").textContent = d.model_a;
    $("#sb-b .sf-name").textContent = d.model_b;
    $("#sb-a .sf-score").textContent = d.score_a;
    $("#sb-b .sf-score").textContent = d.score_b;
    setApiCalls(d.api_calls);
    if (d.game_index) $(".score-game").textContent = "Game " + d.game_index;
    // Pakai white_is_a (slot) bila ada agar tetap benar saat kedua model identik;
    // jatuh balik ke perbandingan nama untuk kompatibilitas.
    if (typeof d.white_is_a === "boolean") {
      $("#sb-a .sf-color").dataset.color = d.white_is_a ? "White" : "Black";
      $("#sb-b .sf-color").dataset.color = d.white_is_a ? "Black" : "White";
    } else if (d.white_model) {
      $("#sb-a .sf-color").dataset.color = d.model_a === d.white_model ? "White" : "Black";
      $("#sb-b .sf-color").dataset.color = d.model_b === d.white_model ? "White" : "Black";
    }
  }

  // Highlight "sedang berpikir" + status; bisa ditunda agar sinkron dgn papan.
  function applyThinking(d) {
    if (d.status) $("#score-status").textContent = d.status;
    var aThinking, bThinking;
    if (d.phase === "thinking" && typeof d.thinking_is_a === "boolean") {
      aThinking = d.thinking_is_a;
      bThinking = !d.thinking_is_a;
    } else {
      aThinking = d.phase === "thinking" && d.thinking_model === d.model_a;
      bThinking = d.phase === "thinking" && d.thinking_model === d.model_b;
    }
    $("#sb-a").classList.toggle("thinking", aThinking);
    $("#sb-b").classList.toggle("thinking", bThinking);
    if (d.phase !== "thinking") {
      $("#sb-a").classList.remove("thinking");
      $("#sb-b").classList.remove("thinking");
    }
  }

  function onState(d) {
    applyStateBase(d);
    // Tunda highlight "berpikir (langkah N+1)" selama papan masih menampilkan/animasi
    // langkah N (antrean belum kosong) agar highlight tidak mendahului papan.
    if (d.phase === "thinking" && (animating || boardQueue.length)) {
      pendingThinkState = d;
    } else {
      pendingThinkState = null;
      applyThinking(d);
    }
  }

  function onLog(d) {
    addLog(d.level || "info", d.message || "");
  }

  function onGameOver(d) {
    $("#sb-a").classList.remove("thinking");
    $("#sb-b").classList.remove("thinking");
    setApiCalls(d.api_calls);
    bumpScore("#sb-a .sf-score", d.score_a);
    bumpScore("#sb-b .sf-score", d.score_b);
    var badge = $("#result-badge");
    if (d.winner_model) { badge.textContent = "Game " + d.game_index + ": " + d.winner_model + " menang"; }
    else { badge.textContent = "Game " + d.game_index + ": remis (" + d.reason + ")"; }
    show(badge);
  }

  function bumpScore(sel, value) {
    var node = $(sel);
    if (node.textContent !== String(value)) {
      node.textContent = value;
      node.classList.add("bump");
      setTimeout(function () { node.classList.remove("bump"); }, 320);
    }
  }

  function onMatchOver(d) {
    matchRunning = false;
    matchPaused = false; setPauseBtn(false); enablePause(false);
    $("#sb-a").classList.remove("thinking");
    $("#sb-b").classList.remove("thinking");
    show($("#back-btn"));
    if (d.aborted) {
      $("#score-status").textContent = "Pertandingan berhenti (" + (d.reason || "") + ")";
      return;
    }
    $("#score-status").textContent = "Sesi selesai";
    var winner = d.winner_model;
    if (winner) {
      $("#winner-name").textContent = winner;
      $("#winner-score").textContent =
        d.model_a + " " + d.score_a + " — " + d.score_b + " " + d.model_b + "  ·  remis " + d.draws;
      show($("#winner-overlay"));
    }
  }

  /* ----------------------------- boot ----------------------------- */
  function connectBridge() {
    new QWebChannel(qt.webChannelTransport, function (channel) {
      bridge = channel.objects.bridge;
      bridge.sig_board.connect(function (s) { onBoard(JSON.parse(s)); });
      bridge.sig_state.connect(function (s) { onState(JSON.parse(s)); });
      bridge.sig_log.connect(function (s) { onLog(JSON.parse(s)); });
      bridge.sig_game_over.connect(function (s) { onGameOver(JSON.parse(s)); });
      bridge.sig_match_over.connect(function (s) { onMatchOver(JSON.parse(s)); });
      bridge.sig_models.connect(function (s) { onModels(JSON.parse(s)); });
      bridge.sig_api_status.connect(function (s) { onApiStatus(JSON.parse(s)); });
      bridge.ready();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    board = new ChessBoard($("#board"));
    initTheme();
    initSound();
    setupCombo("a");
    setupCombo("b");
    setupApiKey();
    setupSettings();
    setupThink();
    $("#start-btn").addEventListener("click", onStart);
    $("#resume-new").addEventListener("click", discardSavedSession);
    $("#refresh-btn").addEventListener("click", function () { if (bridge) bridge.request_models(); });
    $("#pause-btn").addEventListener("click", togglePause);
    $("#stop-btn").addEventListener("click", function () { if (bridge) bridge.stop_match(); });
    $("#back-btn").addEventListener("click", backToSetup);
    $("#winner-close").addEventListener("click", backToSetup);
    $("#think-collapse").addEventListener("click", toggleCollapseAll);
    $("#export-txt").addEventListener("click", function () { exportReasoning("txt"); });
    $("#export-json").addEventListener("click", function () { exportReasoning("json"); });
    if (typeof QWebChannel !== "undefined" && typeof qt !== "undefined") {
      connectBridge();
    } else {
      $("#models-status").textContent = "QWebChannel tidak tersedia (jalankan via aplikasi desktop).";
    }
  });
})();
