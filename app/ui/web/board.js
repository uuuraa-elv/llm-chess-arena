/* Chessboard renderer: render dari FEN + animasi langkah berbasis diff.
   White selalu di bawah. Tidak butuh library eksternal / jQuery.

   Animasi halus: tiap bidak adalah .piece (posisi via transform translate, di-
   transisikan) yang membungkus .piece-body (visual + efek "lift"). Menangani
   slide biasa, makan (fade-out), rokade (dua bidak serempak), en passant, dan
   promosi (pion meluncur lalu "pop" menjadi bidak baru). render() memanggil
   opts.onDone(moveType) setelah animasi selesai untuk sinkronisasi giliran+suara. */
(function (global) {
  "use strict";

  var GLYPH = { K: "♚", Q: "♛", R: "♜", B: "♝", N: "♞", P: "♟" };

  function fenCharToCode(ch) {
    var color = ch === ch.toUpperCase() ? "w" : "b";
    return color + ch.toUpperCase();
  }
  function fileIdxOf(sq) { return sq.charCodeAt(0) - 97; }
  function rankNumOf(sq) { return parseInt(sq.slice(1), 10); }
  function coords(sq) {
    return { x: fileIdxOf(sq) * 100 + "%", y: (8 - rankNumOf(sq)) * 100 + "%" };
  }
  function distance(a, b) {
    return Math.abs(fileIdxOf(a) - fileIdxOf(b)) + Math.abs(rankNumOf(a) - rankNumOf(b));
  }

  function prefersReducedMotion() {
    try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
    catch (e) { return false; }
  }
  function cssMs(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      if (v.endsWith("ms")) return parseFloat(v);
      if (v.endsWith("s")) return parseFloat(v) * 1000;
    } catch (e) {}
    return fallback;
  }

  function ChessBoard(el) {
    this.el = el;
    this.pieces = {};        // square -> { el, code }
    this.useGlyphs = false;
    this.lastFen = null;
    this._gen = 0;           // dinaikkan tiap reset; men-invalidasi callback animasi usang
    this._buildSquares();
    this._detectAssets();
  }

  ChessBoard.prototype._buildSquares = function () {
    var grid = document.createElement("div");
    grid.className = "squares";
    this.squareCells = {};
    for (var r = 8; r >= 1; r--) {
      for (var f = 0; f < 8; f++) {
        var sq = String.fromCharCode(97 + f) + r;
        var cell = document.createElement("div");
        cell.className = "square " + ((f + r) % 2 === 0 ? "dark" : "light");
        cell.dataset.square = sq;
        if (r === 1) {
          var fc = document.createElement("span");
          fc.className = "coord file";
          fc.textContent = String.fromCharCode(97 + f);
          cell.appendChild(fc);
        }
        if (f === 0) {
          var rc = document.createElement("span");
          rc.className = "coord rank";
          rc.textContent = r;
          cell.appendChild(rc);
        }
        grid.appendChild(cell);
        this.squareCells[sq] = cell;
      }
    }
    this.el.appendChild(grid);
  };

  ChessBoard.prototype._detectAssets = function () {
    var self = this;
    var img = new Image();
    img.onerror = function () {
      self.useGlyphs = true;
      if (self.lastFen) self.render(self.lastFen, null, { reset: true });
    };
    img.src = "pieces/wK.svg";
  };

  /* Lukis isi visual bidak (bg SVG atau glyph Unicode) ke elemen body. */
  ChessBoard.prototype._paint = function (body, code) {
    if (this.useGlyphs) {
      var g = body.firstChild;
      if (!g || g.className !== "glyph") {
        body.textContent = "";
        g = document.createElement("span");
        g.className = "glyph";
        body.appendChild(g);
      }
      g.textContent = GLYPH[code[1]];
      if (code[0] === "w") {
        g.style.color = "#f4efe6";
        g.style.textShadow = "0 0 1px #2a2520, 0 1px 2px rgba(0,0,0,.5)";
      } else {
        g.style.color = "#15110d";
        g.style.textShadow = "0 0 1px #cdbfa8";
      }
      body.style.backgroundImage = "";
    } else {
      body.style.backgroundImage = 'url("pieces/' + code + '.svg")';
    }
  };

  ChessBoard.prototype._makePiece = function (code, sq) {
    var el = document.createElement("div");
    el.className = "piece";
    var body = document.createElement("div");
    body.className = "piece-body";
    el.appendChild(body);
    el._body = body;
    this._paint(body, code);
    this._place(el, sq);
    this.el.appendChild(el);
    return el;
  };

  ChessBoard.prototype._place = function (el, sq) {
    var c = coords(sq);
    el.style.setProperty("--x", c.x);
    el.style.setProperty("--y", c.y);
  };

  /* Tandai bidak sebagai sedang bergerak (memicu efek "lift"); lepas saat selesai. */
  ChessBoard.prototype._lift = function (el) {
    el.classList.remove("moving");
    void el.offsetWidth;            // reflow agar animasi lift bisa di-retrigger
    el.classList.add("moving");
    var ms = (prefersReducedMotion() ? 0 : cssMs("--move-dur", 300)) + 60;
    setTimeout(function () { el.classList.remove("moving"); }, ms);
  };

  ChessBoard.prototype._parseFen = function (fen) {
    var map = {};
    var parts = fen.split(" ");
    var rows = parts[0].split("/");
    for (var r = 0; r < 8; r++) {
      var rankNum = 8 - r;
      var fileIdx = 0;
      for (var i = 0; i < rows[r].length; i++) {
        var ch = rows[r][i];
        if (ch >= "1" && ch <= "8") {
          fileIdx += parseInt(ch, 10);
        } else {
          map[String.fromCharCode(97 + fileIdx) + rankNum] = fenCharToCode(ch);
          fileIdx++;
        }
      }
    }
    return { map: map, side: parts[1] || "w" };
  };

  ChessBoard.prototype._clearPieces = function () {
    for (var sq in this.pieces) {
      if (this.pieces[sq].el && this.pieces[sq].el.parentNode) {
        this.pieces[sq].el.parentNode.removeChild(this.pieces[sq].el);
      }
    }
    this.pieces = {};
  };

  ChessBoard.prototype._clearMarks = function () {
    for (var sq in this.squareCells) {
      this.squareCells[sq].classList.remove("hl-from", "hl-to", "check");
    }
  };

  /* render(fen, lastMoveUci, opts): opts.reset = posisi baru tanpa animasi diff,
     opts.check = raja sisi-jalan dalam skak, opts.onDone(moveType) dipanggil
     setelah animasi langkah selesai (untuk sinkronisasi giliran + suara). */
  ChessBoard.prototype.render = function (fen, lastMove, opts) {
    opts = opts || {};
    this.lastFen = fen;
    var parsed = this._parseFen(fen);
    var target = parsed.map;
    this._clearMarks();

    var moveType = "move";
    if (opts.reset) {
      this._gen++;          // batalkan callback animasi langkah game sebelumnya
      this._clearPieces();
      for (var s in target) {
        this.pieces[s] = { el: this._makePiece(target[s], s), code: target[s] };
      }
    } else {
      moveType = this._diffAnimate(target, lastMove);
    }

    var from = null, to = null;
    if (lastMove && lastMove.length >= 4) {
      from = lastMove.slice(0, 2); to = lastMove.slice(2, 4);
      if (this.squareCells[from]) this.squareCells[from].classList.add("hl-from");
      if (this.squareCells[to]) this.squareCells[to].classList.add("hl-to");
    }
    if (opts.check) {
      var kingCode = (parsed.side === "w" ? "w" : "b") + "K";
      for (var ks in target) {
        if (target[ks] === kingCode) { this.squareCells[ks].classList.add("check"); break; }
      }
    }

    if (typeof opts.onDone === "function") {
      var moverEl = (to && this.pieces[to]) ? this.pieces[to].el : null;
      var gen = this._gen, self = this;
      this._whenSettled(moverEl, function () {
        if (gen === self._gen) opts.onDone(moveType);  // abaikan callback usang pasca-reset
      });
    }
  };

  /* Jalankan cb setelah bidak penggerak menyelesaikan transisinya (atau timeout). */
  ChessBoard.prototype._whenSettled = function (el, cb) {
    var done = false;
    function fin() { if (done) return; done = true; cb(); }
    var reduced = prefersReducedMotion();
    var fallbackMs = reduced ? 30 : cssMs("--move-dur", 300) + 90;
    if (!el) { setTimeout(fin, reduced ? 0 : fallbackMs); return; }
    function onEnd(e) {
      if (e.propertyName && e.propertyName !== "transform") return;
      el.removeEventListener("transitionend", onEnd);
      fin();
    }
    el.addEventListener("transitionend", onEnd);
    setTimeout(function () { el.removeEventListener("transitionend", onEnd); fin(); }, fallbackMs);
  };

  ChessBoard.prototype._diffAnimate = function (target, lastMove) {
    var prev = this.pieces;
    var next = {};
    var sq, moveType = "move";
    var consumedPrev = {}, consumedTarget = {};

    // --- Promosi: pion meluncur ke kotak tujuan lalu "pop" jadi bidak baru. ---
    if (lastMove && lastMove.length === 5) {
      var pf = lastMove.slice(0, 2), pt = lastMove.slice(2, 4);
      if (prev[pf] && target[pt]) {
        var promoted = target[pt];
        var pel = prev[pf].el;
        var captured = prev[pt];           // promosi sambil makan (mis. e7xd8Q)
        this._place(pel, pt);
        this._lift(pel);
        this._morph(pel, promoted);
        next[pt] = { el: pel, code: promoted };
        consumedPrev[pf] = true;
        consumedTarget[pt] = true;
        if (captured && !consumedPrev[pt]) {
          consumedPrev[pt] = true;
          this._fadeOut(captured.el);
        }
        moveType = "promotion";
      }
    }

    var removed = [], added = [];
    for (sq in prev) {
      if (consumedPrev[sq]) continue;
      if (target[sq] === prev[sq].code) {
        next[sq] = prev[sq];               // tidak berubah
      } else {
        removed.push({ sq: sq, code: prev[sq].code, el: prev[sq].el });
      }
    }
    for (sq in target) {
      if (consumedTarget[sq]) continue;
      if (!prev[sq] || prev[sq].code !== target[sq] || consumedPrev[sq]) {
        if (!(next[sq])) added.push({ sq: sq, code: target[sq] });
      }
    }

    var self = this;
    var slides = 0;
    added.forEach(function (a) {
      var bestIdx = -1, bestDist = Infinity;
      for (var i = 0; i < removed.length; i++) {
        if (removed[i].used || removed[i].code !== a.code) continue;
        var d = distance(removed[i].sq, a.sq);
        if (d < bestDist) { bestDist = d; bestIdx = i; }
      }
      if (bestIdx >= 0) {
        var r = removed[bestIdx];
        r.used = true;
        self._place(r.el, a.sq);           // animasi via transition (slide)
        self._lift(r.el);
        slides++;
        next[a.sq] = { el: r.el, code: a.code };
      } else {
        var el = self._makePiece(a.code, a.sq);
        el.classList.add("fade-in");
        next[a.sq] = { el: el, code: a.code };
      }
    });

    var fadedOut = 0;
    removed.forEach(function (r) {
      if (r.used) return;
      self._fadeOut(r.el);
      fadedOut++;
    });

    this.pieces = next;

    // Klasifikasi jenis langkah untuk suara (promosi sudah ditangani di atas).
    if (moveType !== "promotion") {
      if (fadedOut > 0) moveType = "capture";          // makan / en passant
      else if (slides >= 2) moveType = "castle";       // dua bidak serempak
      else if (lastMove && lastMove.length >= 4) {
        var ff = lastMove.slice(0, 2), tt = lastMove.slice(2, 4);
        var moverCode = next[tt] && next[tt].code;
        if (moverCode && moverCode[1] === "K" &&
            Math.abs(fileIdxOf(ff) - fileIdxOf(tt)) === 2) {
          moveType = "castle";
        }
      }
    }
    return moveType;
  };

  ChessBoard.prototype._fadeOut = function (el) {
    el.classList.add("fade-out");
    var ms = (prefersReducedMotion() ? 0 : cssMs("--cap-dur", 200)) + 40;
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, ms);
  };

  /* Ganti visual bidak (untuk promosi) dengan "pop" singkat saat mendarat. */
  ChessBoard.prototype._morph = function (el, code) {
    var self = this;
    var ms = prefersReducedMotion() ? 0 : cssMs("--move-dur", 300);
    setTimeout(function () {
      self._paint(el._body, code);
      el.classList.remove("piece-pop");
      void el.offsetWidth;
      el.classList.add("piece-pop");
      setTimeout(function () { el.classList.remove("piece-pop"); }, 260);
    }, ms);
  };

  ChessBoard.prototype.reset = function () {
    this._gen++;
    this._clearPieces();
    this._clearMarks();
    this.lastFen = null;
  };

  global.ChessBoard = ChessBoard;
})(window);
