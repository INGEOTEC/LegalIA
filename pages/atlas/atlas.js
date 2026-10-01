// The Atlas of Mexican federal law (issue #245).
//
// A hand-written D3 v7 application over one file, `atlas.json`, which
// `scripts/embeddings/export_atlas_data.py` exports (issue #244): 1,303
// instruments, each with its provisions (`p`), the weight other instruments'
// provisions send it (`in`), its five closest instruments (`out`) and the five
// that point at it hardest (`inc`), as `[id, weight]` pairs, plus four
// layouts of the same map keyed by neighbourhood size.
//
// Deliberately no framework and no build step: the site is published by a
// runner that has Quarto and nothing else, so what is committed here is what
// the browser runs.
//
// A weight under *Closest instruments* is a button (issue #250): it opens a
// native <dialog> listing the provisions behind that number, read from one
// file per pair, `<data-pairs>/<i>-<j>.json` (issue #249; `data-pairs` on the
// mount, default `atlas/pairs/`). A file is fetched on the first click only,
// once per page load, and checked against this map by both instruments'
// `k`. The mount's `data-page-size` (default 50) is how many rows the table
// shows at a time; it exists so the page's own tests can page a small table.
//
// Several ways of measuring similarity (issues #262, #268). The mount's
// `data-src`/`data-pairs` name the default model's files, and `data-models` (a
// JSON list of `{label, src, pairs}`, the first entry being that same default)
// names every data set the visitor can switch to: two embedding models and a
// BM25 baseline. The pair table's column reads "Score" (one decimal) when the
// set on screen says `meta.method` is `bm25`, "Similarity" (three) otherwise. The other
// files are fetched on the first switch only, and refused unless it lists the same instruments at the same
// positions (`k`) and has the current layout; a mount without `data-models`
// gets no model control at all.
(function () {
  "use strict";

  // The site's categorical palette ("a colour means the same thing across
  // the site"), not a charting library's defaults.
  const COLLECTIONS = {
    leyes: { color: "#2a78d6", one: "Law", many: "laws" },
    reglamentos: { color: "#008300", one: "Regulation", many: "regulations" },
    lineamientos: { color: "#e87ba4", one: "Guideline", many: "guidelines" },
  };
  const INK = "#1f1e1b";
  const DEFAULT_NEIGHBOURHOOD = 16;
  const OPACITY = 0.8;
  const FADED = 0.15;
  const DIMMED = 0.07;
  const MIN_RADIUS = 2.5;
  const SIZE_LEGEND = [10, 100, 1000];
  const SUGGESTIONS = 8;
  const TRANSITION_MS = 700;
  const CAPTION =
    "How many neighbours each instrument is placed against when the map is " +
    "drawn. Small values keep tight local groups; large values keep the " +
    "overall shape. The layout changes, the data does not.";
  const EMPTY =
    "Search for an instrument or click a point to see which laws, " +
    "regulations and guidelines its provisions are closest to.";
  const MODEL_CAPTION =
    "The method that measured how similar two texts are; the 0.6B is the " +
    "default. Switching it changes which instruments are closest, so both the " +
    "relations the panel lists and the layout change; the neighbourhood size " +
    "changes the layout only.";
  const PAGE_SIZE = 50;
  const NOT_AVAILABLE = "The explanation for this pair is not available.";
  const OTHER_VERSION = "The explanation was built for a different version of the map.";

  const mount = document.getElementById("atlas");
  if (!mount) return;

  // Accents and case never decide a match: "constitucion" finds
  // "CONSTITUCIÓN".
  function fold(text) {
    return text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  }

  function formatNumber(value) {
    return Number.isInteger(value) ? d3.format(",")(value) : d3.format(",.1f")(value);
  }

  function provisions(value) {
    return `${formatNumber(value)} provision${value === 1 ? "" : "s"}`;
  }

  function element(tag, attributes, children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attributes || {})) {
      if (key === "text") node.textContent = value;
      else if (key === "className") node.className = value;
      else node.setAttribute(key, value);
    }
    for (const child of children || []) node.append(child);
    return node;
  }

  // A segmented control: one `role="radiogroup"` of `role="radio"` buttons
  // with a roving tabindex and arrow-key handling, shared by the
  // neighbourhood size and the embedding model. `onSelect(value)` receives
  // the option's string value on a click or an arrow key; the caller decides
  // when the choice really is made and then calls `check(value)`, so a model
  // that fails to load never looks selected. `ends` names the two italic end
  // labels of a scale ("local" ... "global"); a control without them has
  // plain rounded ends.
  function radiogroup({ labelId, options, checked, onSelect, ends }) {
    const node = element("div", {
      className: ends ? "atlas-segmented" : "atlas-segmented atlas-segmented-plain",
      role: "radiogroup",
      "aria-labelledby": labelId,
    });
    const buttons = options.map(({ value, text }) =>
      element("button", {
        type: "button",
        role: "radio",
        className: "atlas-segment",
        "data-value": value,
        "aria-checked": String(value === checked),
        tabindex: value === checked ? "0" : "-1",
        text,
      })
    );
    const end = (text) => element("span", { className: "atlas-end", text, "aria-hidden": "true" });
    node.append(...(ends ? [end(ends[0])] : []), ...buttons, ...(ends ? [end(ends[1])] : []));
    buttons.forEach((button, n) => {
      button.addEventListener("click", () => onSelect(button.dataset.value));
      button.addEventListener("keydown", (event) => {
        const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        const next = buttons[(n + step + buttons.length) % buttons.length];
        next.focus();
        onSelect(next.dataset.value);
      });
    });
    function check(value) {
      buttons.forEach((button) => {
        const on = button.dataset.value === value;
        button.setAttribute("aria-checked", String(on));
        button.tabIndex = on ? 0 : -1;
      });
    }
    return { node, check };
  }

  // The models the mount offers, or [] when it offers a single one. Anything
  // malformed is ignored rather than half-built: the page then behaves as it
  // did before the control existed.
  function readModels() {
    if (!mount.dataset.models) return [];
    try {
      const models = JSON.parse(mount.dataset.models);
      const ok = Array.isArray(models) && models.length > 1 &&
        models.every((m) => m && m.label && m.src && m.pairs);
      return ok ? models : [];
    } catch (error) {
      return [];
    }
  }

  function build(data) {
    const items = data.instruments.map((entry, i) => ({
      i,
      c: entry.c,
      k: entry.k,
      n: entry.n,
      p: entry.p,
      in: entry.in,
      out: entry.out,
      inc: entry.inc,
      folded: fold(entry.n),
      foldedKey: fold(entry.k),
    }));
    // The data of the model on screen; `data` stays the default model's.
    let current = data;
    const models = readModels();
    let modelIndex = 0;
    const layouts = Object.keys(data.projections).map(Number).sort((a, b) => a - b);
    let layout = layouts.includes(DEFAULT_NEIGHBOURHOOD)
      ? DEFAULT_NEIGHBOURHOOD
      : layouts[Math.floor(layouts.length / 2)];
    // Every point's current place in [0, 1] x [0, 1], the thing a layout
    // switch interpolates; screen positions are derived from it on demand.
    items.forEach((d) => {
      [d.bx, d.by] = data.projections[String(layout)][d.i];
    });

    let selected = null;
    let focus = null; // a collection shown alone, or null
    let transform = d3.zoomIdentity;
    let width = 0;
    let height = 0;
    let radius = d3.scaleSqrt();
    const x = d3.scaleLinear().domain([0, 1]);
    const y = d3.scaleLinear().domain([0, 1]);

    // -- the markup the application owns ------------------------------------ //
    mount.replaceChildren();
    const input = element("input", {
      type: "search",
      id: "atlas-search",
      className: "atlas-search-input",
      placeholder: "Find a law, regulation or guideline",
      autocomplete: "off",
      spellcheck: "false",
      role: "combobox",
      "aria-autocomplete": "list",
      "aria-expanded": "false",
      "aria-controls": "atlas-suggestions",
      "aria-label": "Find an instrument by name or abbreviation",
    });
    const suggestions = element("ul", {
      id: "atlas-suggestions",
      className: "atlas-suggestions",
      role: "listbox",
      hidden: "",
    });
    const search = element("div", { className: "atlas-search" }, [input, suggestions]);

    const neighbourhoodGroup = radiogroup({
      labelId: "atlas-neighbourhood-label",
      options: layouts.map((value) => ({ value: String(value), text: String(value) })),
      checked: String(layout),
      ends: ["local", "global"],
      onSelect: (value) => setLayout(Number(value)),
    });
    const neighbourhood = element("div", { className: "atlas-neighbourhood" }, [
      element("span", {
        id: "atlas-neighbourhood-label",
        className: "atlas-control-label",
        text: "Neighbourhood size",
      }),
      neighbourhoodGroup.node,
      element("p", { className: "atlas-caption", text: CAPTION }),
    ]);
    // The embedding model control exists only when the mount lists models.
    const modelStatus = element("p", {
      className: "atlas-caption atlas-model-status",
      role: "status",
      "aria-live": "polite",
      hidden: "",
    });
    const modelGroup = models.length
      ? radiogroup({
          labelId: "atlas-model-label",
          options: models.map((model, n) => ({ value: String(n), text: model.label })),
          checked: "0",
          onSelect: (value) => setModel(Number(value)),
        })
      : null;
    const modelControl = modelGroup
      ? element("div", { className: "atlas-model" }, [
          element("span", {
            id: "atlas-model-label",
            className: "atlas-control-label",
            text: "Similarity",
          }),
          modelGroup.node,
          element("p", { className: "atlas-caption", text: MODEL_CAPTION }),
          modelStatus,
        ])
      : null;
    const controls = element(
      "div",
      { className: "atlas-controls" },
      modelControl ? [neighbourhood, modelControl] : [neighbourhood]
    );
    const toolbar = element("div", { className: "atlas-toolbar" }, [search, controls]);

    const svgNode = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svgNode.setAttribute("class", "atlas-map");
    svgNode.setAttribute("role", "img");
    svgNode.setAttribute(
      "aria-label",
      `Map of ${items.length} federal instruments; select one with the search box`
    );
    const label = element("div", { className: "atlas-label", "aria-hidden": "true", hidden: "" });
    const collectionLegend = element("div", {
      className: "atlas-collections",
      role: "group",
      "aria-label": "Show one collection",
    });
    const sizeLegend = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    sizeLegend.setAttribute("class", "atlas-sizes");
    sizeLegend.setAttribute("aria-label", "Circle area is proportional to provisions");
    const legend = element("div", { className: "atlas-legend" }, [collectionLegend, sizeLegend]);
    const mapWrap = element("div", { className: "atlas-map-wrap" }, [svgNode, label]);
    // A <div role="complementary">, never an <aside>: Quarto's page CSS sends
    // every `aside` to the page margin (`grid-column: body-end/page-end
    // !important`), which inside `.atlas-main` left the map a 0 px track.
    const panel = element("div", {
      className: "atlas-panel",
      role: "complementary",
      "aria-live": "polite",
      "aria-label": "Instrument details",
    });
    const main = element("div", { className: "atlas-main" }, [
      element("div", { className: "atlas-map-column" }, [mapWrap, legend]),
      panel,
    ]);
    // One dialog, reused for every pair: the provisions behind a weight.
    const closeButton = element("button", {
      type: "button",
      className: "atlas-explain-close",
      "aria-label": "Close",
      text: "\u00d7",
    });
    const explainBody = element("div", { className: "atlas-explain-body" });
    const dialog = element(
      "dialog",
      { className: "atlas-explain", "aria-labelledby": "atlas-explain-title" },
      [closeButton, explainBody]
    );
    mount.append(toolbar, main, dialog);

    // -- the map --------------------------------------------------------------- //
    const svg = d3.select(svgNode);
    const background = svg.append("rect").attr("class", "atlas-background");
    const pointLayer = svg.append("g").attr("class", "atlas-points");
    const linkLayer = svg.append("g").attr("class", "atlas-links");
    const overlay = svg.append("g").attr("class", "atlas-overlay");

    // Largest first, so a small instrument is drawn on top of a large one and
    // can still be clicked.
    const drawOrder = items.slice().sort((a, b) => b.p - a.p || a.i - b.i);
    const circles = pointLayer
      .selectAll("circle")
      .data(drawOrder, (d) => d.i)
      .join("circle")
      .attr("class", "atlas-point")
      .attr("data-i", (d) => d.i)
      .attr("data-k", (d) => d.k)
      .attr("fill", (d) => COLLECTIONS[d.c].color)
      .on("pointerenter", (event, d) => showLabel(event, d))
      .on("pointermove", (event, d) => showLabel(event, d))
      .on("pointerleave", hideLabel)
      .on("click", (event, d) => {
        event.stopPropagation();
        select(d.i, { reveal: false });
      });

    const byId = new Map(circles.nodes().map((node) => [Number(node.dataset.i), node]));

    function screen(d) {
      return [transform.applyX(x(d.bx)), transform.applyY(y(d.by))];
    }

    function resize() {
      width = mapWrap.clientWidth || 800;
      const tall = window.innerHeight || 800;
      height = Math.max(360, Math.min(Math.round(tall * 0.7), Math.round(width * 1.1)));
      svg.attr("width", width).attr("height", height).attr("viewBox", `0 0 ${width} ${height}`);
      background.attr("width", width).attr("height", height);
      // Area proportional to provisions: the radius is a square root, about
      // 22 px for the largest instrument on an 1,100 px map, never under
      // 2.5 px so a two-provision guideline stays clickable.
      const largest = Math.max(10, Math.min(28, (22 * width) / 1100));
      radius = d3.scaleSqrt().domain([0, d3.max(items, (d) => d.p)]).range([0, largest]);
      const margin = largest + 8;
      x.range([margin, width - margin]);
      y.range([height - margin, margin]);
      zoom.extent([[0, 0], [width, height]]).translateExtent([[0, 0], [width, height]]);
      circles.attr("r", (d) => Math.max(MIN_RADIUS, radius(d.p)));
      drawSizeLegend();
      place();
    }

    function place() {
      circles.each(function (d) {
        const [cx, cy] = screen(d);
        this.setAttribute("cx", cx.toFixed(1));
        this.setAttribute("cy", cy.toFixed(1));
      });
      drawSelection();
    }

    const zoom = d3
      .zoom()
      .scaleExtent([1, 12])
      .on("zoom", (event) => {
        // Applied to positions, not to a group transform: zooming separates
        // points instead of inflating them, and radii stay in screen pixels.
        transform = event.transform;
        hideLabel();
        place();
      });
    svg.call(zoom).on("dblclick.zoom", null);
    svg.on("click", () => clear());

    function showLabel(event, d) {
      const [px, py] = d3.pointer(event, mapWrap);
      label.textContent = d.n;
      label.hidden = false;
      const right = px + 14 + label.offsetWidth > mapWrap.clientWidth;
      label.style.left = `${right ? px - 14 - label.offsetWidth : px + 14}px`;
      label.style.top = `${Math.max(0, py - 12 - label.offsetHeight)}px`;
      d3.select(event.currentTarget).classed("atlas-hover", true);
    }

    function hideLabel() {
      label.hidden = true;
      circles.classed("atlas-hover", false);
    }

    // -- opacity: collection focus, then selection ------------------------------ //
    function restyle() {
      const related = new Set();
      if (selected !== null) {
        related.add(selected);
        items[selected].out.forEach(([j]) => related.add(j));
      }
      circles.attr("opacity", (d) => {
        let value = OPACITY;
        if (focus && d.c !== focus) value = DIMMED;
        if (selected !== null && !related.has(d.i)) value = Math.min(value, FADED);
        if (selected !== null && related.has(d.i)) value = 1;
        return value;
      });
      collectionLegend.querySelectorAll("button").forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.c === focus));
        button.classList.toggle("atlas-muted", Boolean(focus) && button.dataset.c !== focus);
      });
    }

    // -- the selection drawn on the map ----------------------------------------- //
    function drawSelection() {
      if (selected === null) {
        linkLayer.selectAll("*").remove();
        overlay.selectAll("*").remove();
        return;
      }
      const source = items[selected];
      const [sx, sy] = screen(source);
      const rs = Math.max(MIN_RADIUS, radius(source.p));
      const targets = source.out.map(([j, w], rank) => {
        const target = items[j];
        const [tx, ty] = screen(target);
        const rt = Math.max(MIN_RADIUS, radius(target.p));
        const length = Math.hypot(tx - sx, ty - sy) || 1;
        const ux = (tx - sx) / length;
        const uy = (ty - sy) / length;
        return {
          j, w, rank: rank + 1,
          x1: sx + ux * rs, y1: sy + uy * rs,
          x2: tx - ux * rt, y2: ty - uy * rt,
          // The number sits just past the target, on the line's own axis.
          bx: tx + ux * (rt + 10), by: ty + uy * (rt + 10),
        };
      });

      linkLayer
        .selectAll("line")
        .data(targets, (t) => t.j)
        .join("line")
        .attr("class", "atlas-link")
        .attr("data-rank", (t) => t.rank)
        .attr("x1", (t) => t.x1)
        .attr("y1", (t) => t.y1)
        .attr("x2", (t) => t.x2)
        .attr("y2", (t) => t.y2);

      const badges = overlay
        .selectAll("g.atlas-badge")
        .data(targets, (t) => t.j)
        .join((enter) => {
          const g = enter.append("g").attr("class", "atlas-badge");
          g.append("circle").attr("r", 8.5);
          g.append("text").attr("dy", "0.35em");
          return g;
        })
        .attr("transform", (t) => `translate(${t.bx},${t.by})`);
      badges.select("text").text((t) => t.rank);

      overlay
        .selectAll("circle.atlas-ring")
        .data([source])
        .join("circle")
        .attr("class", "atlas-ring")
        .attr("cx", sx)
        .attr("cy", sy)
        .attr("r", rs + 3.5);

      const name = source.n.length > 70 ? `${source.n.slice(0, 68)}…` : source.n;
      const text = overlay
        .selectAll("text.atlas-name")
        .data([source])
        .join("text")
        .attr("class", "atlas-name")
        .text(name);
      // Centred over the point, but kept inside the map: shifted sideways at
      // an edge, and drawn under the point when there is no room above it.
      let length = text.node().getComputedTextLength();
      for (let keep = name.length - 2; length > width - 8 && keep > 8; keep -= 2) {
        text.text(`${source.n.slice(0, keep)}…`);
        length = text.node().getComputedTextLength();
      }
      const left = Math.max(4, Math.min(width - 4 - length, sx - length / 2));
      const above = sy - rs - 10;
      text.attr("x", left).attr("y", above < 16 ? sy + rs + 20 : above);
    }

    // -- the detail panel ------------------------------------------------------- //
    // `from` is the selected instrument when the list is its *Closest
    // instruments*: only those weights have a file behind them, so only they
    // become buttons. *Points here* keeps plain numbers.
    function targetList(pairs, total, className, from) {
      const list = element("ol", { className: `atlas-targets ${className}` });
      pairs.forEach(([j, w]) => {
        const target = items[j];
        const share = total > 0 ? Math.min(100, (100 * w) / total) : 0;
        const button = element("button", {
          type: "button",
          className: "atlas-target-name",
          "data-i": String(j),
          text: target.n,
        });
        button.addEventListener("click", () => select(j, { reveal: true }));
        let weight;
        if (from === undefined) {
          weight = element("span", { className: "atlas-weight", text: w.toFixed(1) });
        } else {
          weight = element("button", {
            type: "button",
            className: "atlas-weight atlas-why",
            "data-i": String(j),
            "aria-haspopup": "dialog",
            title: `Why these provisions are closest to ${target.n}`,
            "aria-label": `${w.toFixed(1)}: why these provisions are closest to ${target.n}`,
            text: w.toFixed(1),
          });
          weight.addEventListener("click", () => explain(from, j, weight));
        }
        list.append(
          element("li", { className: "atlas-target" }, [
            weight,
            element("span", { className: "atlas-bar", "aria-hidden": "true" }, [
              element("span", {
                className: "atlas-bar-fill",
                style: `width:${share.toFixed(1)}%;background:${COLLECTIONS[target.c].color}`,
              }),
            ]),
            element("span", { className: "atlas-target-label" }, [
              element("span", {
                className: "atlas-dot",
                style: `background:${COLLECTIONS[target.c].color}`,
                "aria-hidden": "true",
              }),
              button,
            ]),
          ])
        );
      });
      return list;
    }

    function fillPanel() {
      panel.replaceChildren();
      if (selected === null) {
        panel.append(element("p", { className: "atlas-empty", text: EMPTY }));
        return;
      }
      const d = items[selected];
      const collection = COLLECTIONS[d.c];
      const identifier = d.c === "leyes" ? "abbreviation" : "SCJN id";
      panel.append(
        element("span", {
          className: "atlas-badge-label",
          style: `background:${collection.color}`,
          text: collection.one,
        }),
        element("h2", { className: "atlas-panel-title", text: d.n }),
        element("p", { className: "atlas-facts" }, [
          `${identifier} `,
          element("em", { text: d.k }),
          " · ",
          element("strong", { text: provisions(d.p) }),
        ]),
        element("h3", { className: "atlas-panel-heading", text: "Closest instruments" }),
        element("p", {
          className: "atlas-note",
          text: `where its ${provisions(d.p)}' nearest texts live`,
        }),
        targetList(d.out, d.p, "atlas-out", d.i),
        element("h3", { className: "atlas-panel-heading", text: "Points here" })
      );
      if (d.inc.length) {
        panel.append(
          element("p", {
            className: "atlas-note",
            text: `${provisions(d.in)} of other instruments have their closest text in this one`,
          }),
          targetList(d.inc, d.in, "atlas-inc")
        );
      } else {
        panel.append(
          element("p", {
            className: "atlas-note",
            text: "No provision of another instrument has its closest text in this one.",
          })
        );
      }
    }

    // -- the explanation dialog ------------------------------------------------ //
    const pairsOf = (n) =>
      new URL(n === 0 ? mount.dataset.pairs || "atlas/pairs/" : models[n].pairs, document.baseURI);
    let pairsBase = pairsOf(0);
    const pageSize = Math.max(1, parseInt(mount.dataset.pageSize, 10) || PAGE_SIZE);
    const explanations = new Map(); // "<model>:<i>-<j>" -> the promise of its file
    let opener = null;
    let showing = 0; // which request the dialog is waiting for

    // md2akn's Markdown carries `**bold**` and little else: that is all this
    // renders. Everything else is text, never HTML; a blank line starts a
    // paragraph. Nothing is shortened.
    function renderText(text) {
      const fragment = document.createDocumentFragment();
      String(text || "")
        .split(/\n\s*\n/)
        .forEach((paragraph) => {
          if (!paragraph.trim()) return;
          const parts = paragraph.split("**");
          // An unpaired `**` stays as written.
          if (parts.length % 2 === 0) {
            const tail = parts.pop();
            parts[parts.length - 1] += `**${tail}`;
          }
          const node = element("p", { className: "atlas-explain-text" });
          parts.forEach((part, n) => {
            if (part) node.append(n % 2 ? element("strong", { text: part }) : part);
          });
          fragment.append(node);
        });
      return fragment;
    }

    function loadPair(i, j) {
      const file = `${i}-${j}`;
      const key = `${modelIndex}:${file}`;
      if (!explanations.has(key)) {
        const request = fetch(new URL(`${file}.json`, pairsBase)).then((response) => {
          if (!response.ok) throw new Error(`${response.status}`);
          return response.json();
        });
        // A failure is not remembered: the next click may try again.
        request.catch(() => explanations.delete(key));
        explanations.set(key, request);
      }
      return explanations.get(key);
    }

    function explainTitle(source, target) {
      return element("h2", { id: "atlas-explain-title", className: "atlas-explain-title" }, [
        "Why ",
        element("em", { text: source.n }),
        " is close to ",
        element("em", { text: target.n }),
      ]);
    }

    function status(text) {
      return element("p", { className: "atlas-explain-status", text });
    }

    function provisionCell(label, path, text) {
      const cell = [element("span", { className: "atlas-explain-label", text: label })];
      if (path) cell.push(element("span", { className: "atlas-explain-path", text: path }));
      cell.push(renderText(text));
      return cell;
    }

    // What the pair file's `similarity` is depends on how the data set on screen
    // scored its texts, which the file itself says (`meta.method`): a lexical
    // BM25 score is unbounded, a cosine of two embeddings lies in 0..1. A file
    // with no `method` predates the field and is an embedding.
    function scoreLabel() {
      return current.meta && current.meta.method === "bm25" ? "Score" : "Similarity";
    }

    function formatScore(value) {
      return Number(value).toFixed(scoreLabel() === "Score" ? 1 : 3);
    }

    function explanationRow(pair, row, n, target) {
      const texts = pair.texts || {};
      const targets = row.targets || [];
      const closest = [];
      if (targets.length) {
        const first = targets[0];
        closest.push(...provisionCell(first.label, first.path, texts[first.text]));
        const others = targets.length - 1;
        if (others > 0) {
          const same = targets.every((entry) => entry.text === first.text);
          const provisionWord = others === 1 ? "provision" : "provisions";
          const verb = same ? (others === 1 ? "carries this text" : "carry this text")
            : (others === 1 ? "is as close" : "are as close");
          closest.push(element("span", {
            className: "atlas-explain-also",
            text: `also ${others} other ${provisionWord} of ${target.n} ${verb}`,
          }));
        }
      }
      const weight = [element("span", {
        className: "atlas-explain-fraction",
        text: row.m === 1 ? "1" : `1/${row.m}`,
      })];
      if (row.m > 1) {
        weight.push(element("span", {
          className: "atlas-explain-share",
          text: `this text is shared by ${row.m} instruments`,
        }));
      }
      return element("tr", {}, [
        element("td", { className: "atlas-explain-n", "data-column": "#", text: String(n + 1) }),
        element("td", { className: "atlas-explain-source", "data-column": "Provision" },
          provisionCell(row.label, row.path, texts[row.text])),
        element("td", { className: "atlas-explain-target", "data-column": "Closest text" },
          closest),
        element("td", {
          className: "atlas-explain-similarity",
          "data-column": scoreLabel(),
          text: formatScore(row.similarity),
        }),
        element("td", { className: "atlas-explain-weight", "data-column": "Weight" }, weight),
      ]);
    }

    function renderPair(pair, source, target) {
      const rows = pair.rows || [];
      const count = element("p", { className: "atlas-explain-count" });
      const tbody = element("tbody");
      const more = element("button", { type: "button", className: "atlas-explain-more" });
      let shown = 0;

      function showMore() {
        const next = Math.min(rows.length, shown + pageSize);
        for (let n = shown; n < next; n += 1) {
          tbody.append(explanationRow(pair, rows[n], n, target));
        }
        shown = next;
        const left = rows.length - shown;
        count.textContent = `Showing ${d3.format(",")(shown)} of ${d3.format(",")(rows.length)}`;
        more.hidden = left === 0;
        more.textContent =
          `Show ${d3.format(",")(Math.min(pageSize, left))} more (${d3.format(",")(left)} left)`;
      }
      more.addEventListener("click", showMore);

      const table = element("table", { className: "atlas-explain-table" }, [
        element("thead", {}, [
          element("tr", {}, [
            element("th", { className: "atlas-explain-n", scope: "col", text: "#" }),
            element("th", { scope: "col", text: `Provision of ${source.n}` }),
            element("th", { scope: "col", text: `Closest text in ${target.n}` }),
            element("th", { className: "atlas-explain-similarity", scope: "col", text: scoreLabel() }),
            element("th", { className: "atlas-explain-weight", scope: "col", text: "Weight" }),
          ]),
        ]),
        tbody,
      ]);
      explainBody.replaceChildren(
        explainTitle(source, target),
        element("p", { className: "atlas-explain-lead" }, [
          `${provisions(pair.provisions)} of `,
          element("em", { text: source.n }),
          " have their closest text outside it in ",
          element("em", { text: target.n }),
          `; they add up to ${Number(pair.weight).toFixed(1)} of its ${provisions(source.p)}.`,
        ]),
        count,
        table,
        more
      );
      showMore();
    }

    function explain(i, j, button) {
      const source = items[i];
      const target = items[j];
      const request = ++showing;
      opener = button;
      explainBody.replaceChildren(explainTitle(source, target), status("Loading\u2026"));
      if (!dialog.open) dialog.showModal();
      dialog.scrollTop = 0;
      loadPair(i, j)
        .then((pair) => {
          if (request !== showing) return;
          if (!pair || !pair.source || !pair.target ||
              pair.source.k !== source.k || pair.target.k !== target.k) {
            explainBody.replaceChildren(explainTitle(source, target), status(OTHER_VERSION));
            return;
          }
          renderPair(pair, source, target);
        })
        .catch(() => {
          if (request !== showing) return;
          explainBody.replaceChildren(explainTitle(source, target), status(NOT_AVAILABLE));
        });
    }

    closeButton.addEventListener("click", () => dialog.close());
    // A click on the backdrop reaches the dialog itself, outside its box.
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      const inside = event.clientX >= box.left && event.clientX <= box.right &&
        event.clientY >= box.top && event.clientY <= box.bottom;
      if (!inside) dialog.close();
    });
    dialog.addEventListener("close", () => {
      showing += 1; // a late answer must not fill a closed dialog
      if (opener && opener.isConnected) opener.focus();
      opener = null;
    });

    // -- selecting ------------------------------------------------------------- //
    // Zoom so the instrument and its five closest instruments all fit: the
    // relation is what was asked for, and centring on the point alone left
    // most of its lines running off the map.
    function reveal(d) {
      const members = [d, ...d.out.map(([j]) => items[j])];
      const [x0, x1] = d3.extent(members, (m) => x(m.bx));
      const [y0, y1] = d3.extent(members, (m) => y(m.by));
      const pad = 70;
      const k = Math.max(1, Math.min(4,
        (width - 2 * pad) / Math.max(x1 - x0, 1),
        (height - 2 * pad) / Math.max(y1 - y0, 1)));
      const target = d3.zoomIdentity
        .translate(width / 2 - (k * (x0 + x1)) / 2, height / 2 - (k * (y0 + y1)) / 2)
        .scale(k);
      svg.transition().duration(600).call(zoom.transform, target);
    }

    function select(i, options) {
      selected = i;
      const d = items[i];
      // A collection shown alone would hide the instrument just asked for.
      if (focus && focus !== d.c) focus = null;
      byId.get(i).parentNode.appendChild(byId.get(i));
      restyle();
      drawSelection();
      fillPanel();
      if (options && options.reveal) reveal(d);
    }

    function clear() {
      if (selected === null) return;
      selected = null;
      restyle();
      drawSelection();
      fillPanel();
    }

    // -- the collection legend ------------------------------------------------- //
    Object.entries(COLLECTIONS).forEach(([c, collection]) => {
      const count = data.meta.collections[c] || 0;
      const button = element(
        "button",
        {
          type: "button",
          className: "atlas-collection",
          "data-c": c,
          "aria-pressed": "false",
        },
        [
          element("span", {
            className: "atlas-dot",
            style: `background:${collection.color}`,
            "aria-hidden": "true",
          }),
          `${d3.format(",")(count)} ${collection.many}`,
        ]
      );
      button.addEventListener("click", () => {
        focus = focus === c ? null : c;
        restyle();
      });
      collectionLegend.append(button);
    });

    function drawSizeLegend() {
      const radii = SIZE_LEGEND.map((p) => Math.max(MIN_RADIUS, radius(p)));
      const tall = Math.ceil(2 * radii[radii.length - 1]) + 4;
      const legendSvg = d3.select(sizeLegend);
      legendSvg.selectAll("*").remove();
      let offset = 2;
      const group = legendSvg.append("g");
      SIZE_LEGEND.forEach((p, n) => {
        const r = radii[n];
        group
          .append("circle")
          .attr("cx", offset + r)
          .attr("cy", tall / 2)
          .attr("r", r)
          .attr("class", "atlas-size");
        const text = group
          .append("text")
          .attr("x", offset + 2 * r + 5)
          .attr("y", tall / 2)
          .attr("dy", "0.35em")
          .text(d3.format(",")(p));
        offset += 2 * r + 5 + text.node().getComputedTextLength() + 14;
      });
      const caption = group
        .append("text")
        .attr("x", offset)
        .attr("y", tall / 2)
        .attr("dy", "0.35em")
        .attr("class", "atlas-size-caption")
        .text("provisions");
      offset += caption.node().getComputedTextLength() + 2;
      legendSvg.attr("width", Math.ceil(offset)).attr("height", tall);
    }

    // -- the neighbourhood control and the embedding model ---------------------- //
    // Every point moves from where it is to `positions[i]` over one
    // transition; a layout switch and a model switch use the same one.
    function moveTo(positions) {
      items.forEach((d) => {
        d.fromX = d.bx;
        d.fromY = d.by;
        [d.toX, d.toY] = positions[d.i];
      });
      hideLabel();
      d3.select(mount)
        .transition("layout")
        .duration(TRANSITION_MS)
        .ease(d3.easeCubicInOut)
        .tween("layout", () => (t) => {
          items.forEach((d) => {
            d.bx = d.fromX + (d.toX - d.fromX) * t;
            d.by = d.fromY + (d.toY - d.fromY) * t;
          });
          place();
        });
    }

    function setLayout(value) {
      if (value === layout) return;
      layout = value;
      neighbourhoodGroup.check(String(value));
      moveTo(current.projections[String(value)]);
    }

    const modelFiles = new Map([[0, data]]); // model index -> its parsed file
    const modelRequests = new Map(); // model index -> the promise of its file
    let switching = 0; // which model switch is current; older ones are dropped

    function loadModel(n) {
      if (modelFiles.has(n)) return Promise.resolve(modelFiles.get(n));
      if (!modelRequests.has(n)) {
        const request = fetch(new URL(models[n].src, document.baseURI))
          .then((response) => {
            if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
            return response.json();
          })
          .then((file) => {
            modelFiles.set(n, file);
            return file;
          });
        // A failure is not remembered: choosing the model again retries.
        request.catch(() => modelRequests.delete(n));
        modelRequests.set(n, request);
      }
      return modelRequests.get(n);
    }

    // Why a file cannot replace the map on screen, or null: `i` is positional
    // in the file, in the layouts and in every pair file, so the instruments
    // must be the same ones in the same order.
    function incompatibility(file) {
      const entries = file && file.instruments;
      const same = Array.isArray(entries) && entries.length === items.length &&
        items.every((d) => entries[d.i] && entries[d.i].k === d.k);
      if (!same) return "lists different instruments";
      if (!file.projections || !file.projections[String(layout)]) return "has no such layout";
      return null;
    }

    function setModelStatus(text) {
      modelStatus.textContent = text;
      modelStatus.hidden = !text;
    }

    function applyModel(n, file) {
      current = file;
      modelIndex = n;
      pairsBase = pairsOf(n);
      showing += 1; // an answer still on its way belongs to the old model
      items.forEach((d) => {
        const entry = file.instruments[d.i];
        d.p = entry.p;
        d.in = entry.in;
        d.out = entry.out;
        d.inc = entry.inc;
      });
      resize();
      restyle();
      drawSelection();
      fillPanel();
      moveTo(file.projections[String(layout)]);
      showGenerated();
    }

    function setModel(n) {
      const request = ++switching;
      if (n === modelIndex) {
        // Back to the model on screen: whatever was still loading is dropped.
        setModelStatus("");
        modelGroup.node.removeAttribute("aria-busy");
        return Promise.resolve();
      }
      const name = models[n].label;
      setModelStatus(`Loading the ${name} map\u2026`);
      modelGroup.node.setAttribute("aria-busy", "true");
      return loadModel(n)
        .then((file) => {
          if (request !== switching) return;
          const reason = incompatibility(file);
          if (reason) {
            setModelStatus(`The ${name} map cannot be used: it ${reason}.`);
            return;
          }
          modelGroup.check(String(n));
          setModelStatus("");
          applyModel(n, file);
        })
        .catch(() => {
          if (request !== switching) return;
          setModelStatus(`The ${name} map could not be loaded. Choose it again to retry.`);
        })
        .finally(() => {
          if (request === switching) modelGroup.node.removeAttribute("aria-busy");
        });
    }

    // -- search ---------------------------------------------------------------- //
    let matches = [];
    let active = -1;

    function find(query) {
      const folded = fold(query).trim();
      const words = folded.split(/\s+/).filter(Boolean);
      if (!words.length) return [];
      const found = [];
      for (const d of items) {
        let inName = true;
        let ok = true;
        for (const word of words) {
          const name = d.folded.includes(word);
          if (!name) inName = false;
          if (!name && !d.foldedKey.includes(word)) {
            ok = false;
            break;
          }
        }
        if (!ok) continue;
        // Exact-prefix name matches first, then a name that contains every
        // word, then an abbreviation or id.
        const rank = d.folded.startsWith(folded) || d.foldedKey === folded ? 0 : inName ? 1 : 2;
        found.push({ d, rank });
      }
      found.sort((a, b) => a.rank - b.rank || b.d.p - a.d.p || a.d.i - b.d.i);
      return found.slice(0, SUGGESTIONS).map((entry) => entry.d);
    }

    function closeSuggestions() {
      suggestions.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    }

    function highlight(n) {
      active = n;
      suggestions.querySelectorAll("li").forEach((li, m) => {
        li.setAttribute("aria-selected", String(m === n));
      });
      if (n >= 0) input.setAttribute("aria-activedescendant", `atlas-suggestion-${n}`);
      else input.removeAttribute("aria-activedescendant");
    }

    function choose(d) {
      closeSuggestions();
      input.value = d.n;
      select(d.i, { reveal: true });
    }

    function showSuggestions() {
      matches = find(input.value);
      suggestions.replaceChildren();
      if (!matches.length) {
        if (input.value.trim()) {
          suggestions.append(
            element("li", { className: "atlas-no-match", text: "No instrument matches." })
          );
          suggestions.hidden = false;
          input.setAttribute("aria-expanded", "true");
        } else {
          closeSuggestions();
        }
        return;
      }
      matches.forEach((d, n) => {
        const li = element(
          "li",
          {
            id: `atlas-suggestion-${n}`,
            className: "atlas-suggestion",
            role: "option",
            "aria-selected": "false",
            "data-i": String(d.i),
          },
          [
            element("span", {
              className: "atlas-dot",
              style: `background:${COLLECTIONS[d.c].color}`,
              "aria-hidden": "true",
            }),
            element("span", { className: "atlas-suggestion-name", text: d.n }),
            element("span", {
              className: "atlas-suggestion-collection",
              text: COLLECTIONS[d.c].one,
            }),
          ]
        );
        // `mousedown`, not `click`: it lands before the input's blur closes
        // the list.
        li.addEventListener("mousedown", (event) => {
          event.preventDefault();
          choose(d);
        });
        suggestions.append(li);
      });
      suggestions.hidden = false;
      input.setAttribute("aria-expanded", "true");
      highlight(0);
    }

    input.addEventListener("input", showSuggestions);
    input.addEventListener("focus", () => {
      if (input.value.trim()) showSuggestions();
    });
    input.addEventListener("blur", () => closeSuggestions());
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (suggestions.hidden) showSuggestions();
        if (!matches.length) return;
        const step = event.key === "ArrowDown" ? 1 : -1;
        highlight((active + step + matches.length) % matches.length);
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (suggestions.hidden) showSuggestions();
        if (matches.length) choose(matches[Math.max(0, active)]);
      } else if (event.key === "Escape") {
        if (!suggestions.hidden) {
          event.stopPropagation();
          closeSuggestions();
        } else {
          clear();
        }
      }
    });

    document.addEventListener("keydown", (event) => {
      // An open dialog takes Escape for itself: it closes, the selection stays.
      if (event.key === "Escape" && event.target !== input && !dialog.open) clear();
    });

    // The date the map on screen was generated, wherever the page shows it.
    function showGenerated() {
      document.querySelectorAll("[data-atlas-generated]").forEach((node) => {
        const date = new Date(current.meta.generated);
        node.textContent = Number.isNaN(date.getTime())
          ? current.meta.generated
          : date.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
      });
    }

    // -- start ----------------------------------------------------------------- //
    resize();
    restyle();
    fillPanel();
    if (typeof ResizeObserver === "function") {
      let last = mapWrap.clientWidth;
      new ResizeObserver(() => {
        if (mapWrap.clientWidth !== last) {
          last = mapWrap.clientWidth;
          resize();
        }
      }).observe(mapWrap);
    } else {
      window.addEventListener("resize", resize);
    }

    showGenerated();
    mount.dataset.ready = "true";
  }

  const source = new URL(mount.dataset.src || "atlas/atlas.json", document.baseURI);
  fetch(source)
    .then((response) => {
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.json();
    })
    .then(build)
    .catch((error) => {
      mount.replaceChildren(
        element("p", {
          className: "atlas-error",
          text: `The map could not be loaded (${error.message}).`,
        })
      );
      mount.dataset.ready = "error";
    });
})();
