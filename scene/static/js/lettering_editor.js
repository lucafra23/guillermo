/* Visual lettering editor.
 *
 * Coordinates are FRACTIONS of the image (0..1), because that is what the compositor consumes and
 * what lets one spec letter a thumbnail and a 3x print plate identically. Everything here converts
 * between those fractions and screen pixels at the last possible moment, so a resized browser
 * window or a different plate resolution changes nothing about what gets stored.
 *
 * The textarea is the single source of truth for the form. The editor reads it on load and writes
 * it on every change, so a failure in this file degrades to hand-editing JSON rather than to
 * silent data loss.
 *
 * Preview composites through the REAL letterer on the server. An approximation drawn in the
 * browser would be worse than nothing: the whole point of the vector letterer is that what you
 * approve is exactly what gets written.
 */
(function () {
  "use strict";

  var TAILED = { bubble: true, ui: true, thought: true };

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

  function readJSON(text) {
    if (!text || !text.trim()) { return []; }
    try {
      var parsed = JSON.parse(text);
      if (Array.isArray(parsed)) { return parsed; }
      if (parsed && Array.isArray(parsed.elements)) { return parsed.elements; }
      return [];
    } catch (e) {
      return null; // signals "unparseable": leave the raw text alone rather than destroying it
    }
  }

  function Editor(root) {
    this.root = root;
    this.textarea = root.querySelector(".lettering-json");
    this.stage = root.querySelector(".lettering-stage");
    this.overlay = root.querySelector(".lettering-overlay");
    this.list = root.querySelector(".lettering-list");
    this.plate = root.querySelector(".lettering-plate");
    this.status = root.querySelector(".lettering-status");
    this.previewUrl = root.dataset.previewUrl;
    this.actionId = root.dataset.actionId;
    this.plateSrc = root.dataset.plate;
    this.selected = -1;

    var parsed = readJSON(this.textarea.value);
    if (parsed === null) {
      this.broken = true;
      this.say("The stored lettering is not valid JSON. Fix it under 'Raw JSON' to use the editor.", true);
      return;
    }
    this.elements = parsed;

    this.bind();
    this.render();
  }

  Editor.prototype.say = function (message, isError) {
    this.status.textContent = message || "";
    this.status.classList.toggle("is-error", !!isError);
  };

  Editor.prototype.bind = function () {
    var self = this;

    this.root.querySelector(".lettering-add").addEventListener("click", function () {
      var type = self.root.querySelector(".lettering-add-type").value;
      var el = { type: type, text: "New text", box: [0.1, 0.1, 0.5, 0.14] };
      if (TAILED[type]) { el.tail = [0.5, 0.5]; }
      self.elements.push(el);
      self.selected = self.elements.length - 1;
      self.commit();
    });

    this.root.querySelector(".lettering-preview").addEventListener("click", function () {
      self.preview();
    });

    this.root.querySelector(".lettering-revert").addEventListener("click", function () {
      if (self.plate && self.plateSrc) { self.plate.src = self.plateSrc; }
      self.say("");
    });

    // Hand-edits to the JSON win: re-read when the raw box loses focus.
    this.textarea.addEventListener("change", function () {
      var parsed = readJSON(self.textarea.value);
      if (parsed === null) { self.say("Not valid JSON.", true); return; }
      self.elements = parsed;
      self.selected = -1;
      self.render();
      self.say("");
    });
  };

  /** Write the model back to the textarea, then redraw. */
  Editor.prototype.commit = function () {
    this.textarea.value = JSON.stringify(this.elements, null, 2);
    this.render();
  };

  Editor.prototype.render = function () {
    if (this.broken) { return; }
    this.renderBoxes();
    this.renderList();
  };

  Editor.prototype.renderBoxes = function () {
    var self = this;
    this.overlay.innerHTML = "";
    this.elements.forEach(function (el, i) {
      var box = el.box || [0, 0, 0.2, 0.1];
      var node = document.createElement("div");
      node.className = "lettering-box" + (i === self.selected ? " is-selected" : "");
      node.style.left = (box[0] * 100) + "%";
      node.style.top = (box[1] * 100) + "%";
      node.style.width = (box[2] * 100) + "%";
      node.style.height = (box[3] * 100) + "%";
      node.dataset.index = i;

      var label = document.createElement("span");
      label.className = "lettering-box-label";
      label.textContent = el.type;
      node.appendChild(label);

      var handle = document.createElement("span");
      handle.className = "lettering-resize";
      node.appendChild(handle);

      node.addEventListener("mousedown", function (ev) {
        if (ev.target === handle) { self.startDrag(ev, i, "resize"); }
        else { self.startDrag(ev, i, "move"); }
      });

      self.overlay.appendChild(node);

      if (el.tail) {
        var tail = document.createElement("div");
        tail.className = "lettering-tail" + (i === self.selected ? " is-selected" : "");
        tail.style.left = (el.tail[0] * 100) + "%";
        tail.style.top = (el.tail[1] * 100) + "%";
        tail.title = el.type + " tail — drag to the speaker's mouth";
        tail.addEventListener("mousedown", function (ev) { self.startDrag(ev, i, "tail"); });
        self.overlay.appendChild(tail);
      }
    });
  };

  Editor.prototype.startDrag = function (ev, index, mode) {
    ev.preventDefault();
    ev.stopPropagation();
    var self = this;
    var rect = this.stage.getBoundingClientRect();
    if (!rect.width || !rect.height) { return; }
    var el = this.elements[index];
    var startX = ev.clientX, startY = ev.clientY;
    var origin = mode === "tail" ? el.tail.slice() : el.box.slice();
    this.selected = index;

    function onMove(e) {
      var dx = (e.clientX - startX) / rect.width;
      var dy = (e.clientY - startY) / rect.height;
      if (mode === "move") {
        // Allowed to overhang the edge: balloons legitimately bleed off the plate.
        el.box[0] = clamp(origin[0] + dx, -0.5, 1.5);
        el.box[1] = clamp(origin[1] + dy, -0.5, 1.5);
      } else if (mode === "resize") {
        el.box[2] = clamp(origin[2] + dx, 0.02, 2);
        el.box[3] = clamp(origin[3] + dy, 0.02, 2);
      } else {
        el.tail[0] = clamp(origin[0] + dx, -0.5, 1.5);
        el.tail[1] = clamp(origin[1] + dy, -0.5, 1.5);
      }
      self.textarea.value = JSON.stringify(self.elements, null, 2);
      self.renderBoxes();
    }

    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      self.commit();
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  Editor.prototype.renderList = function () {
    var self = this;
    this.list.innerHTML = "";

    if (!this.elements.length) {
      var empty = document.createElement("p");
      empty.className = "lettering-empty";
      empty.textContent = "No lettering on this panel. It will composite to nothing, and the bare plate is used.";
      this.list.appendChild(empty);
      return;
    }

    this.elements.forEach(function (el, i) {
      var row = document.createElement("div");
      row.className = "lettering-row" + (i === self.selected ? " is-selected" : "");

      var head = document.createElement("div");
      head.className = "lettering-row-head";

      var type = document.createElement("select");
      ["bubble", "thought", "caption", "plaque", "ui", "namecard", "screen"].forEach(function (t) {
        var opt = document.createElement("option");
        opt.value = t; opt.textContent = t;
        if (t === el.type) { opt.selected = true; }
        type.appendChild(opt);
      });
      type.addEventListener("change", function () {
        el.type = type.value;
        // A tail only means something on a tailed container; adding/removing it here keeps the
        // stored spec honest rather than carrying a coordinate nothing will ever draw.
        if (TAILED[el.type] && !el.tail) { el.tail = [0.5, 0.5]; }
        if (!TAILED[el.type]) { delete el.tail; }
        self.commit();
      });
      head.appendChild(type);

      var del = document.createElement("button");
      del.type = "button";
      del.className = "lettering-delete";
      del.textContent = "Delete";
      del.addEventListener("click", function () {
        self.elements.splice(i, 1);
        self.selected = -1;
        self.commit();
      });
      head.appendChild(del);
      row.appendChild(head);

      var text = document.createElement("textarea");
      text.className = "lettering-text";
      text.rows = 2;
      text.value = el.text || "";
      text.placeholder = el.type === "namecard" ? "NAME|one-line descriptor" : "words on the panel";
      text.addEventListener("input", function () {
        el.text = text.value;
        self.textarea.value = JSON.stringify(self.elements, null, 2);
      });
      text.addEventListener("focus", function () { self.selected = i; self.renderBoxes(); });
      row.appendChild(text);

      self.list.appendChild(row);
    });
  };

  Editor.prototype.preview = function () {
    var self = this;
    if (!this.previewUrl || !this.actionId) {
      this.say("Save the panel once before previewing.", true);
      return;
    }
    this.say("Compositing…");
    var body = new FormData();
    body.append("action_id", this.actionId);
    body.append("lettering", this.textarea.value);
    var token = document.querySelector("[name=csrfmiddlewaretoken]");
    if (token) { body.append("csrfmiddlewaretoken", token.value); }

    fetch(this.previewUrl, { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { self.say(data.error, true); return; }
        // Cache-bust: the preview path is stable per action, so the browser would show the last one.
        self.plate.src = data.url + "?t=" + Date.now();
        self.say("Preview composited. Nothing is saved until you save the panel.");
      })
      .catch(function (e) { self.say("Preview failed: " + e, true); });
  };

  function init() {
    document.querySelectorAll(".lettering-editor").forEach(function (root) {
      if (!root.dataset.ready) { root.dataset.ready = "1"; new Editor(root); }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
