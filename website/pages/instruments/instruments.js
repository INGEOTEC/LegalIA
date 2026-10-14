/* The Instruments page (issue #290): every instrument of the Atlas, read the way
   md2akn segmented it. One IIFE, no library, no build step; it reads only the
   `data-*` attributes of its mount (the index, the folder of per-instrument files,
   the repository to report problems to). The files are the ones
   scripts/embeddings/export_instrument_files.py writes (issue #289): the raw
   document once, md2akn's tree and its excerpts as UTF-16 offsets into it, parse
   flags and, on every compared excerpt, its nearest texts.

   The Atlas' combobox and segmented control are rebuilt here on purpose, not
   shared: both pages are build-less scripts and a shared one would couple the
   Atlas' pinned script tags to this page. */
(function () {
  "use strict";

  // The site's categorical palette ("a colour means the same thing across the
  // site"), the same as the Atlas'.
  const COLLECTIONS = {
    leyes: { color: "#2a78d6", one: "Law", many: "laws" },
    reglamentos: { color: "#008300", one: "Regulation", many: "regulations" },
    lineamientos: { color: "#e87ba4", one: "Guideline", many: "guidelines" },
    tratados: { color: "#eda100", one: "Treaty", many: "treaties" },
  };
  // md2akn's Akoma Ntoso vocabulary and the Mexican term for each type.
  const TERMS = {
    act: "instrumento",
    preamble: "preámbulo",
    body: "cuerpo",
    conclusions: "conclusiones",
    book: "libro",
    title: "título",
    chapter: "capítulo",
    section: "sección",
    level: "apartado",
    article: "artículo",
    paragraph: "fracción",
    point: "inciso",
    subpoint: "subinciso",
    content: "texto",
  };
  const CONTAINERS = new Set(["book", "title", "chapter", "section", "level"]);
  const FLAG_ORDER = [
    "loose",
    "duplicate_number",
    "numbering_gap",
    "over_window",
    "no_articles",
    "uncovered",
    "no_transitorios",
  ];
  const FLAG_LABELS = {
    loose: "Loose",
    duplicate_number: "Repeated number",
    numbering_gap: "Number gap",
    over_window: "Over window",
    no_articles: "No articles",
    uncovered: "Uncovered",
    no_transitorios: "No transitory section",
  };
  const SUGGESTIONS = 8;
  const PAGE_SIZE = 50;
  const BATCH = 300;
  const NOT_AVAILABLE = "This instrument's file is not available.";
  const OTHER_VERSION = "This instrument's file was built from a different version of the data.";

  const mount = document.getElementById("instruments");
  if (!mount) return;

  const INDEX_URL = new URL(mount.dataset.index || "instruments/instruments.json", document.baseURI);
  const FILES_URL = new URL(mount.dataset.files || "instruments/files/", document.baseURI);
  const REPO = (mount.dataset.repo || "https://github.com/INGEOTEC/LegalIA").replace(/\/+$/, "");

  // Accents and case never decide a match: "constitucion" finds "CONSTITUCIÓN".
  function fold(text) {
    return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  function number(value) {
    return Number(value).toLocaleString("en-US");
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

  function dot(collection) {
    return element("span", {
      className: "instruments-dot",
      style: `background:${COLLECTIONS[collection].color}`,
      "aria-hidden": "true",
    });
  }

  // A segmented control: one `role="radiogroup"` of `role="radio"` buttons with a
  // roving tabindex and arrow-key handling (the Atlas' pattern).
  function radiogroup({ labelId, options, checked, onSelect }) {
    const node = element("div", {
      className: "instruments-segmented",
      role: "radiogroup",
      "aria-labelledby": labelId,
    });
    const buttons = options.map(({ value, text }) =>
      element("button", {
        type: "button",
        role: "radio",
        className: "instruments-segment",
        "data-value": value,
        "aria-checked": String(value === checked),
        tabindex: value === checked ? "0" : "-1",
        text,
      })
    );
    node.append(...buttons);
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
    return {
      node,
      check(value) {
        buttons.forEach((button) => {
          const on = button.dataset.value === value;
          button.setAttribute("aria-checked", String(on));
          button.tabIndex = on ? 0 : -1;
        });
      },
    };
  }

  // md2akn's Markdown carries `**bold**` and little else: that is all this
  // renders. Everything else is text, never HTML; a blank line starts a paragraph.
  function appendInline(node, text) {
    const parts = text.split("**");
    // An unpaired `**` stays as written.
    if (parts.length % 2 === 0) {
      const tail = parts.pop();
      parts[parts.length - 1] += `**${tail}`;
    }
    parts.forEach((part, n) => {
      if (part) node.append(n % 2 ? element("strong", { text: part }) : part);
    });
    return node;
  }

  function renderText(text) {
    const fragment = document.createDocumentFragment();
    String(text || "")
      .split(/\n\s*\n/)
      .forEach((paragraph) => {
        if (!paragraph.trim()) return;
        fragment.append(appendInline(element("p", { className: "instruments-paragraph" }), paragraph.trim()));
      });
    return fragment;
  }

  // A table node (md2akn's rule 13) is a run of `| a | b |` blocks, one block per row,
  // with the odd label (`**(ARANCEL)**`) between two of them. This returns its blocks
  // with the offset each starts at, so a row can be matched to the excerpt it lies in.
  function tableBlocks(markdown, node) {
    const text = markdown.slice(node.s, node.l);
    const blocks = [];
    const separator = /\n[ \t\r]*\n/g;
    let from = 0;
    const push = (end) => {
      const raw = text.slice(from, end);
      const body = raw.trim();
      if (body) blocks.push({ s: node.s + from + (raw.length - raw.trimStart().length), text: body });
    };
    let match;
    while ((match = separator.exec(text))) {
      push(match.index);
      from = match.index + match[0].length;
    }
    push(text.length);
    return blocks;
  }

  // The cells of one row. scjn writes a literal pipe inside a cell as `\|`.
  function tableCells(row) {
    const cells = [];
    let cell = "";
    for (let q = 1; q < row.length - 1; q++) {
      const ch = row[q];
      if (ch === "\\" && row[q + 1] === "|") {
        cell += "|";
        q++;
      } else if (ch === "|") {
        cells.push(cell.trim());
        cell = "";
      } else {
        cell += ch;
      }
    }
    cells.push(cell.trim());
    return cells;
  }

  const IS_ROW = /^\|[^]*\|$/;

  // The HTML table of some blocks. A block that is not a row is a one-cell row that
  // spans the table, so nothing md2akn put in the node is dropped from the page.
  function renderTable(blocks) {
    const rows = blocks.map((block) => (IS_ROW.test(block.text) ? tableCells(block.text) : null));
    const width = Math.max(1, ...rows.map((cells) => (cells ? cells.length : 0)));
    const body = element("tbody");
    blocks.forEach((block, n) => {
      const tr = element("tr", { className: rows[n] ? "instruments-table-row" : "instruments-table-label" });
      if (rows[n]) {
        rows[n].forEach((cell) => tr.append(appendInline(element("td"), cell)));
      } else {
        const td = appendInline(element("td"), block.text);
        td.colSpan = width;
        tr.append(td);
      }
      body.append(tr);
    });
    return element("div", { className: "instruments-table-scroll" }, [
      element("table", { className: "instruments-table" }, [body]),
    ]);
  }

  // The excerpt label of a nearest text, from its eId only (the file of the
  // instrument it lives in is not loaded yet): "Article 27", "Preamble", "Loose
  // text", "Introduction", "Table 1", with `, part 2` for a piece.
  function excerptLabel(eId, piece) {
    const last = String(eId).split("__").pop();
    const transitory = String(eId).startsWith("sec_transitorios");
    let label;
    if (last === "preamble") label = "Preamble";
    else if (last === "conclusions") label = "Closing";
    else if (last === "intro") label = "Introduction";
    else if (last.startsWith("table_")) label = `Table ${last.slice(6)}`;
    else if (last.startsWith("art_")) {
      const num = last.slice(4).split("_").join(" ");
      label = transitory ? `Transitory article ${num}` : `Article ${num}`;
    } else if (last.startsWith("p_")) label = transitory ? "Transitory text" : "Loose text";
    else label = last;
    return piece ? `${label}, part ${piece}` : label;
  }

  function excerptId(unit) {
    return unit.p ? `${unit.e}.${unit.p}` : unit.e;
  }

  // -- the application ------------------------------------------------------- //
  function build(index) {
    const meta = index.meta;
    const entries = index.instruments;
    const items = entries.map((d, i) => ({
      i,
      d,
      c: d.c,
      k: String(d.k),
      n: d.n,
      folded: fold(d.n),
      foldedKey: fold(String(d.k)),
    }));
    for (const item of items) {
      if (!COLLECTIONS[item.c]) {
        throw new Error(`the index has a collection this page does not know: ${item.c}`);
      }
    }
    const byKey = new Map(items.map((item) => [`${item.c}/${item.k}`, item]));
    const flagMeanings = meta.flag_meanings || {};
    const files = new Map();

    mount.replaceChildren();

    // -- search -------------------------------------------------------------- //
    const input = element("input", {
      type: "search",
      id: "instruments-search",
      className: "instruments-search-input",
      placeholder: "Find a law, regulation, guideline or treaty",
      autocomplete: "off",
      spellcheck: "false",
      role: "combobox",
      "aria-autocomplete": "list",
      "aria-expanded": "false",
      "aria-controls": "instruments-suggestions",
      "aria-label": "Find an instrument by name or abbreviation",
    });
    const suggestions = element("ul", {
      id: "instruments-suggestions",
      className: "instruments-suggestions",
      role: "listbox",
      hidden: "",
    });
    const search = element("div", { className: "instruments-search" }, [input, suggestions]);
    const toolbar = element("div", { className: "instruments-toolbar" }, [search]);
    const status = element("p", {
      className: "instruments-status",
      role: "status",
      hidden: "",
    });
    const landing = element("div", { className: "instruments-landing", id: "instruments-landing" });
    const view = element("div", { className: "instruments-view", id: "instruments-view", hidden: "" });
    mount.append(toolbar, status, landing, view);

    function say(message) {
      if (message) {
        status.textContent = message;
        status.hidden = false;
      } else {
        status.textContent = "";
        status.hidden = true;
      }
    }

    // -- the landing view: the corpus table ------------------------------------ //
    const COLUMNS = [
      { key: "n", label: "Instrument", text: true },
      { key: "u", label: "Excerpts" },
      { key: "x", label: "Compared" },
      { key: "a", label: "Articles" },
      ...FLAG_ORDER.map((flag) => ({
        key: flag,
        label: FLAG_LABELS[flag],
        title: flagMeanings[flag] || "",
        flag,
      })),
      { key: "s", label: "Score", title: "Flagged excerpts, plus one for each of no articles and uncovered" },
    ];
    let sortKey = "s";
    let sortDirection = -1;
    let filter = null;
    let shown = PAGE_SIZE;

    const filters = element("div", { className: "instruments-filters", role: "group", "aria-label": "Collection" });
    const filterButtons = Object.entries(COLLECTIONS)
      .filter(([name]) => items.some((item) => item.c === name))
      .map(([name, info]) => {
        const button = element(
          "button",
          { type: "button", className: "instruments-filter", "aria-pressed": "false", "data-collection": name },
          [dot(name), element("span", { text: info.many })]
        );
        button.addEventListener("click", () => {
          filter = filter === name ? null : name;
          filterButtons.forEach((other) =>
            other.setAttribute("aria-pressed", String(other.dataset.collection === filter))
          );
          shown = PAGE_SIZE;
          fillTable();
        });
        return button;
      });
    filters.append(...filterButtons);
    const caption = element("p", {
      className: "instruments-caption",
      text:
        `${number(meta.instruments)} instruments, ${number(meta.units)} excerpts, ` +
        `${number(meta.searched)} of them compared with the rest of the corpus. ` +
        "Click a column to sort; a name opens the instrument.",
    });
    const head = element("tr");
    const headerButtons = COLUMNS.map((column) => {
      const button = element("button", { type: "button", className: "instruments-sort", text: column.label });
      if (column.title) button.setAttribute("title", column.title);
      const cell = element("th", { scope: "col", "data-key": column.key, "aria-sort": "none" }, [button]);
      if (!column.text) cell.className = "instruments-number";
      button.addEventListener("click", () => {
        if (sortKey === column.key) sortDirection = -sortDirection;
        else {
          sortKey = column.key;
          sortDirection = column.text ? 1 : -1;
        }
        shown = PAGE_SIZE;
        fillTable();
      });
      head.append(cell);
      return cell;
    });
    const body = element("tbody");
    const table = element("table", { className: "instruments-ranking" }, [
      element("thead", {}, [head]),
      body,
    ]);
    const tableWrap = element("div", { className: "instruments-table-wrap" }, [table]);
    const more = element("button", { type: "button", className: "instruments-more", text: "Show more" });
    more.addEventListener("click", () => {
      shown += PAGE_SIZE;
      fillTable();
    });
    landing.append(filters, caption, tableWrap, more);

    function valueOf(item, key) {
      if (key === "n") return item.n;
      if (key === "s") return item.d.s;
      if (FLAG_ORDER.includes(key)) return item.d.f[key] || 0;
      return item.d[key];
    }

    function fillTable() {
      const rows = items.filter((item) => !filter || item.c === filter);
      rows.sort((a, b) => {
        const x = valueOf(a, sortKey);
        const y = valueOf(b, sortKey);
        const order =
          typeof x === "string" ? x.localeCompare(y) * sortDirection : (x - y) * sortDirection;
        return order || b.d.s - a.d.s || a.i - b.i;
      });
      headerButtons.forEach((cell) =>
        cell.setAttribute(
          "aria-sort",
          cell.dataset.key === sortKey ? (sortDirection > 0 ? "ascending" : "descending") : "none"
        )
      );
      body.replaceChildren();
      rows.slice(0, shown).forEach((item) => {
        const open = element("button", { type: "button", className: "instruments-open", text: item.n });
        open.addEventListener("click", () => openInstrument(item.c, item.k, { push: true }));
        const row = element("tr", { "data-collection": item.c, "data-key": item.k }, [
          element("td", { className: "instruments-name" }, [dot(item.c), open]),
          element("td", { className: "instruments-number", text: number(item.d.u) }),
          element("td", { className: "instruments-number", text: number(item.d.x) }),
          element("td", { className: "instruments-number", text: number(item.d.a) }),
          ...FLAG_ORDER.map((flag) =>
            element("td", {
              className: "instruments-number",
              text: item.d.f[flag] ? number(item.d.f[flag]) : "",
            })
          ),
          element("td", { className: "instruments-number instruments-score", text: number(item.d.s) }),
        ]);
        body.append(row);
      });
      more.hidden = rows.length <= shown;
      more.textContent = `Show more (${number(rows.length - shown)} left)`;
    }

    // -- search behaviour ------------------------------------------------------ //
    let matches = [];
    let active = -1;

    function find(query) {
      const folded = fold(query).trim();
      const words = folded.split(/\s+/).filter(Boolean);
      if (!words.length) return [];
      const found = [];
      for (const item of items) {
        let inName = true;
        let ok = true;
        for (const word of words) {
          const name = item.folded.includes(word);
          if (!name) inName = false;
          if (!name && !item.foldedKey.includes(word)) {
            ok = false;
            break;
          }
        }
        if (!ok) continue;
        // Exact-prefix name matches first, then a name that contains every word,
        // then an abbreviation or id.
        const rank = item.folded.startsWith(folded) || item.foldedKey === folded ? 0 : inName ? 1 : 2;
        found.push({ item, rank });
      }
      found.sort((a, b) => a.rank - b.rank || b.item.d.u - a.item.d.u || a.item.i - b.item.i);
      return found.slice(0, SUGGESTIONS).map((entry) => entry.item);
    }

    function closeSuggestions() {
      suggestions.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    }

    function highlightSuggestion(n) {
      active = n;
      suggestions.querySelectorAll("li").forEach((li, m) => {
        li.setAttribute("aria-selected", String(m === n));
      });
      if (n >= 0) input.setAttribute("aria-activedescendant", `instruments-suggestion-${n}`);
      else input.removeAttribute("aria-activedescendant");
    }

    function choose(item) {
      closeSuggestions();
      input.value = item.n;
      openInstrument(item.c, item.k, { push: true });
    }

    function showSuggestions() {
      matches = find(input.value);
      suggestions.replaceChildren();
      if (!matches.length) {
        if (input.value.trim()) {
          suggestions.append(
            element("li", { className: "instruments-no-match", text: "No instrument matches." })
          );
          suggestions.hidden = false;
          input.setAttribute("aria-expanded", "true");
        } else {
          closeSuggestions();
        }
        return;
      }
      matches.forEach((item, n) => {
        const li = element(
          "li",
          {
            id: `instruments-suggestion-${n}`,
            className: "instruments-suggestion",
            role: "option",
            "aria-selected": "false",
            "data-key": item.k,
          },
          [
            dot(item.c),
            element("span", { className: "instruments-suggestion-name", text: item.n }),
            element("span", {
              className: "instruments-suggestion-collection",
              text: COLLECTIONS[item.c].one,
            }),
          ]
        );
        // `mousedown`, not `click`: it lands before the input's blur closes the list.
        li.addEventListener("mousedown", (event) => {
          event.preventDefault();
          choose(item);
        });
        suggestions.append(li);
      });
      suggestions.hidden = false;
      input.setAttribute("aria-expanded", "true");
      highlightSuggestion(0);
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
        highlightSuggestion((active + step + matches.length) % matches.length);
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (suggestions.hidden) showSuggestions();
        if (matches.length) choose(matches[Math.max(0, active)]);
      } else if (event.key === "Escape") {
        // A search field clears itself on Escape by default; the first press only
        // closes the list.
        event.preventDefault();
        if (!suggestions.hidden) {
          event.stopPropagation();
          closeSuggestions();
        } else {
          input.value = "";
        }
      }
    });

    // -- loading a file --------------------------------------------------------- //
    function loadFile(item) {
      const key = `${item.c}/${item.k}`;
      if (!files.has(key)) {
        const request = fetch(new URL(`${encodeURIComponent(item.c)}/${encodeURIComponent(item.k)}.json`, FILES_URL))
          .then((response) => {
            if (!response.ok) throw new Error(NOT_AVAILABLE);
            return response.json();
          })
          .then((doc) => {
            // The index and the file must describe the same instrument.
            if (!doc.meta || String(doc.meta.k) !== item.k || doc.meta.c !== item.c) {
              throw new Error(OTHER_VERSION);
            }
            return doc;
          });
        // A failure is not remembered: the next click may try again.
        request.catch(() => files.delete(key));
        files.set(key, request);
      }
      return files.get(key);
    }

    // -- the instrument view ---------------------------------------------------- //
    let current = null; // the instrument on screen: { item, doc, ... }
    let opening = 0; // which request the view is waiting for

    function reportUrl(doc, eId) {
      const m = doc.meta;
      const page =
        `${location.origin}${location.pathname}?c=${encodeURIComponent(m.c)}` +
        `&k=${encodeURIComponent(m.k)}${eId ? `#${encodeURIComponent(eId)}` : ""}`;
      const title = `md2akn: ${m.k} ${m.archivo}${eId ? ` ${eId}` : ""}`;
      const lines = [
        `Collection: ${m.c}`,
        `Key: ${m.k}`,
        `Name: ${m.n}`,
        `Snapshot: ${m.archivo}`,
        `md2akn version: ${m.md2akn_version}`,
      ];
      if (eId) lines.push(`Excerpt or node: ${eId}`);
      lines.push(`Page: ${page}`, "", "What is wrong:", "");
      return `${REPO}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(lines.join("\n"))}`;
    }

    function reportLink(doc, eId) {
      return element(
        "a",
        {
          className: "instruments-report",
          href: reportUrl(doc, eId),
          title: "Report a parsing problem",
          "aria-label": "Report a parsing problem",
          target: "_blank",
          rel: "noopener",
          text: "!",
        },
        []
      );
    }

    function url(c, k, hash) {
      const base = `${location.pathname}?c=${encodeURIComponent(c)}&k=${encodeURIComponent(k)}`;
      return hash ? `${base}#${encodeURIComponent(hash)}` : base;
    }

    function nearHref(c, k, eId, piece) {
      const hash = piece ? `${eId}.${piece}` : eId;
      return `?c=${encodeURIComponent(c)}&k=${encodeURIComponent(k)}#${encodeURIComponent(hash)}`;
    }

    function showLanding() {
      current = null;
      opening += 1;
      view.hidden = true;
      view.replaceChildren();
      landing.hidden = false;
      mount.dataset.view = "landing";
    }

    function openInstrument(c, k, options) {
      const { push, hash } = Object.assign({ push: true, hash: "" }, options);
      const item = byKey.get(`${c}/${k}`);
      if (!item) {
        say(`No instrument "${k}" in the collection "${c}".`);
        if (!current) showLanding();
        return Promise.resolve(false);
      }
      if (current && current.item === item) {
        say("");
        if (push) history.pushState(null, "", url(c, k, hash));
        goToHash(hash);
        return Promise.resolve(true);
      }
      const ticket = ++opening;
      say(`Loading ${item.n}…`);
      return loadFile(item).then(
        (doc) => {
          if (ticket !== opening) return false;
          say("");
          if (push) history.pushState(null, "", url(c, k, hash));
          show(item, doc);
          goToHash(hash);
          return true;
        },
        (error) => {
          if (ticket !== opening) return false;
          say(error.message === OTHER_VERSION ? OTHER_VERSION : NOT_AVAILABLE);
          return false;
        }
      );
    }

    function show(item, doc) {
      current = { item, doc };
      landing.hidden = true;
      view.hidden = false;
      view.replaceChildren();
      mount.dataset.view = "instrument";
      const state = buildView(item, doc);
      current.state = state;
      input.value = item.n;
    }

    function backToTable() {
      history.pushState(null, "", location.pathname);
      say("");
      showLanding();
      input.value = "";
    }

    function buildView(item, doc) {
      const m = doc.meta;
      const markdown = doc.markdown;
      const unitsByExcerpt = new Map();
      const unitsByEid = new Map();
      const tableUnits = new Map(); // a table node's eId -> its excerpts, one per piece
      const flagged = new Map();
      for (const unit of doc.units) {
        unitsByExcerpt.set(excerptId(unit), unit);
        if (unit.u === "table") {
          if (!tableUnits.has(unit.e)) tableUnits.set(unit.e, []);
          tableUnits.get(unit.e).push(unit);
        }
        // A heading and the text under it share an eId when a container carries
        // both; the first excerpt of a node (piece 0) is the one the node shows.
        if (!unitsByEid.has(unit.e)) unitsByEid.set(unit.e, unit);
        for (const flag of unit.f || []) {
          if (!flagged.has(flag)) flagged.set(flag, []);
          flagged.get(flag).push(excerptId(unit));
        }
      }
      const excerptEls = new Map(); // excerpt id -> the element to scroll to
      const nodeEls = new Map(); // eId -> the node element
      const state = { excerptEls, nodeEls, done: false, flagged };

      // -- header ----------------------------------------------------------------- //
      const info = COLLECTIONS[m.c];
      const identifier =
        m.c === "leyes"
          ? ["Abbreviation", m.k]
          : ["SCJN id", m.k];
      const back = element("button", { type: "button", className: "instruments-back", text: "← All instruments" });
      back.addEventListener("click", backToTable);
      const header = element("header", { className: "instruments-head" }, [
        back,
        element("div", { className: "instruments-head-line" }, [
          element(
            "span",
            { className: "instruments-collection", style: `background:${info.color}`, text: info.one }
          ),
          reportLink(doc, ""),
        ]),
        element("h2", { className: "instruments-title", text: m.n }),
        element("p", { className: "instruments-facts" }, [
          `${identifier[0]} `,
          element("strong", { text: m.k }),
          ` · snapshot ${m.fecha_publicacion} (`,
          element("code", { text: m.archivo }),
          `) · md2akn ${m.md2akn_version} · ${number(m.units)} excerpts, ${number(m.searched)} compared`,
        ]),
      ]);

      // -- summary of the parse ---------------------------------------------------- //
      const summary = element("div", { className: "instruments-summary" });
      summary.append(
        element("p", {
          className: "instruments-numbering",
          text:
            `Numbering: ${m.numbering.mode}` +
            (m.numbering.roman_articles ? `, ${number(m.numbering.roman_articles)} roman-numbered articles` : ""),
        })
      );
      const flagList = element("ul", { className: "instruments-flags" });
      for (const flag of FLAG_ORDER) {
        const count = (m.flags && m.flags[flag]) || 0;
        const li = element("li", { className: "instruments-flag-row", "data-flag": flag });
        const label = element("span", { className: "instruments-flag-name", text: FLAG_LABELS[flag], title: flagMeanings[flag] || "" });
        li.append(label);
        const targets = flagged.get(flag) || [];
        if (!count) {
          li.append(element("span", { className: "instruments-flag-none", text: "none" }));
        } else if (targets.length) {
          let at = -1;
          const button = element("button", {
            type: "button",
            className: "instruments-flag-button",
            title: `Go to the first excerpt with this flag; click again for the next`,
            text: `${number(count)}`,
          });
          const position = element("span", { className: "instruments-flag-position" });
          button.addEventListener("click", () => {
            at = (at + 1) % targets.length;
            position.textContent = `${at + 1} of ${targets.length}`;
            jumpToExcerpt(targets[at]);
          });
          li.append(button, position);
        } else if (flag === "uncovered") {
          const button = element("button", {
            type: "button",
            className: "instruments-flag-button",
            title: "Show the raw document at the first uncovered characters",
            text: `${number(count)} characters`,
          });
          button.addEventListener("click", () => {
            setMode("raw");
            const first = rawEl.querySelector(".instruments-uncovered");
            if (first) first.scrollIntoView({ block: "center" });
          });
          li.append(button);
        } else {
          li.append(element("span", { className: "instruments-flag-yes", text: "yes" }));
        }
        flagList.append(li);
      }
      summary.append(flagList);

      // -- the mode control ----------------------------------------------------------- //
      const modeLabel = element("span", { id: "instruments-mode-label", className: "instruments-control-label", text: "Show" });
      const mode = radiogroup({
        labelId: "instruments-mode-label",
        options: [
          { value: "parsed", text: "Parsed" },
          { value: "raw", text: "Raw" },
        ],
        checked: "parsed",
        onSelect: (value) => setMode(value),
      });
      const modeBox = element("div", { className: "instruments-mode" }, [modeLabel, mode.node]);

      // -- the outline and the document ------------------------------------------------ //
      const outlineList = element("ul", { className: "instruments-outline-list" });
      const outlineToggle = element("button", {
        type: "button",
        className: "instruments-outline-toggle",
        "aria-expanded": "false",
        text: "Outline",
      });
      const outline = element("div", { className: "instruments-outline", role: "complementary", "aria-label": "Outline" }, [
        outlineToggle,
        outlineList,
      ]);
      outlineToggle.addEventListener("click", () => {
        const open = outline.dataset.open !== "true";
        outline.dataset.open = String(open);
        outlineToggle.setAttribute("aria-expanded", String(open));
      });
      const documentEl = element("div", { className: "instruments-document", "data-rendered": "false" });
      const rawEl = element("pre", { className: "instruments-rawdoc", hidden: "" });
      const reading = element("div", { className: "instruments-reading" }, [documentEl, rawEl]);
      const layout = element("div", { className: "instruments-layout" }, [outline, reading]);
      view.append(header, summary, modeBox, layout);

      let rawBuilt = false;
      function setMode(value) {
        mode.check(value);
        const raw = value === "raw";
        documentEl.hidden = raw;
        rawEl.hidden = !raw;
        view.dataset.mode = value;
        if (raw && !rawBuilt) {
          rawBuilt = true;
          rawEl.append(buildRaw(doc));
        }
      }
      setMode("parsed");

      // the outline
      function outlineLabel(node) {
        if (node.t === "intro") return "Introduction";
        if (node.t === "table") return `Table ${node.n || ""}`.trim();
        const term = TERMS[node.t] || node.t;
        const name = node.r === "#transitorios" ? "Transitory section" : term.charAt(0).toUpperCase() + term.slice(1);
        const num = node.n && node.r !== "#transitorios" ? ` ${node.n}` : "";
        return `${name}${num}`;
      }

      function addOutline(parent, node, depth) {
        const outlineNode =
          CONTAINERS.has(node.t) ||
          ["article", "preamble", "conclusions", "intro", "table"].includes(node.t);
        if (!outlineNode) {
          for (const child of node.c || []) addOutline(parent, child, depth);
          return;
        }
        const li = element("li", { className: "instruments-outline-item", "data-type": node.t });
        const go = element("button", { type: "button", className: "instruments-outline-link" });
        const unit = unitsByEid.get(node.e);
        if (node.t === "article" || node.t === "intro" || node.t === "table") {
          go.textContent = node.t !== "article" ? outlineLabel(node) : node.n ? `Art. ${node.n}` : node.e;
          if (unit && unit.x) go.prepend(element("span", { className: "instruments-compared", title: "Compared excerpt", "aria-hidden": "true" }));
          if (unit && unit.f && unit.f.length) {
            go.append(element("span", { className: "instruments-mark", title: unit.f.map((f) => FLAG_LABELS[f] || f).join(", "), text: "⚑" }));
          }
        } else {
          go.textContent = outlineLabel(node);
        }
        go.setAttribute("title", node.h ? `${outlineLabel(node)} — ${node.h}` : node.e);
        go.addEventListener("click", () => {
          goTo(node.e);
          outline.dataset.open = "false";
          outlineToggle.setAttribute("aria-expanded", "false");
        });
        li.append(go);
        const children = (node.c || []).filter(() => true);
        if (CONTAINERS.has(node.t)) {
          const inner = element("ul", { className: "instruments-outline-list" });
          for (const child of children) addOutline(inner, child, depth + 1);
          if (inner.children.length) {
            const expanded = depth < 1;
            const toggle = element("button", {
              type: "button",
              className: "instruments-outline-fold",
              "aria-expanded": String(expanded),
              "aria-label": "Expand or collapse",
              text: expanded ? "▾" : "▸",
            });
            inner.hidden = !expanded;
            toggle.addEventListener("click", () => {
              inner.hidden = !inner.hidden;
              toggle.setAttribute("aria-expanded", String(!inner.hidden));
              toggle.textContent = inner.hidden ? "▸" : "▾";
            });
            li.prepend(toggle);
            li.append(inner);
          }
        }
        parent.append(li);
        // An introduction holds the tables that sit in it; they are outline entries of their own.
        if (node.t === "intro") {
          for (const child of node.c || []) if (child.t === "table") addOutline(parent, child, depth);
        }
      }
      for (const child of doc.tree.c || []) addOutline(outlineList, child, 0);
      if (!outlineList.children.length) outline.hidden = true;

      // the document, in chunks
      const order = [];
      (function flatten(node, parentNode) {
        const stack = [[node, parentNode]];
        while (stack.length) {
          const [n, p] = stack.pop();
          order.push([n, p]);
          const kids = n.c || [];
          for (let q = kids.length - 1; q >= 0; q--) stack.push([kids[q], n]);
        }
      })(doc.tree, null);

      const kidsOf = new Map();
      let cursor = 0;

      function nodeText(node) {
        const first = node.c && node.c.length ? node.c[0].s : null;
        return markdown.slice(node.s, first === null ? node.l : Math.max(node.s, first));
      }

      function tagFor(unit) {
        const tags = [element("span", { className: "instruments-tag", text: unit.u })];
        if (unit.p) tags.push(element("span", { className: "instruments-tag", text: `part ${unit.p}` }));
        for (const flag of unit.f || []) {
          tags.push(
            element("span", { className: "instruments-flag-tag", "data-flag": flag, title: flagMeanings[flag] || "", text: FLAG_LABELS[flag] || flag })
          );
        }
        return tags;
      }

      function nearDisclosure(unit) {
        const near = unit.near || [];
        const details = element("details", { className: "instruments-near" }, [
          element("summary", { text: `Nearest texts (${near.length})` }),
        ]);
        let built = false;
        details.addEventListener("toggle", () => {
          if (!details.open || built) return;
          built = true;
          const list = element("ol", { className: "instruments-near-list" });
          for (const [j, eId, piece, similarity, also] of near) {
            const other = items[j];
            const link = (target, targetEid, targetPiece) =>
              element(
                "a",
                {
                  className: "instruments-near-link",
                  href: nearHref(target.c, target.k, targetEid, targetPiece),
                  "data-target-key": target.k,
                  "data-target-eid": targetPiece ? `${targetEid}.${targetPiece}` : targetEid,
                  title: targetEid,
                },
                [dot(target.c), element("span", { text: `${target.n} — ${excerptLabel(targetEid, targetPiece)}` })]
              );
            const li = element("li", { className: "instruments-near-item" }, [
              link(other, eId, piece),
              element("span", { className: "instruments-similarity", text: Number(similarity).toFixed(3) }),
            ]);
            if (also && also.length) {
              const rest = element("span", { className: "instruments-also" }, [
                `also carried by ${also.length} other instrument${also.length === 1 ? "" : "s"}: `,
              ]);
              also.forEach(([k2, eId2, piece2], n) => {
                if (n) rest.append(", ");
                rest.append(link(items[k2], eId2, piece2));
              });
              li.append(rest);
            }
            list.append(li);
          }
          details.append(list);
        });
        return details;
      }

      function makeNode(node, parentNode) {
        const el = element("section", {
          className: `instruments-node instruments-type-${node.t}`,
          "data-eid": node.e,
          "data-type": node.t,
          "data-start": String(node.s),
          "data-end": String(node.l),
        });
        const term = TERMS[node.t] || node.t;
        const badge = element("span", {
          className: "instruments-badge",
          title: node.r === "#transitorios" ? `${term} — transitorios` : `${term}`,
          text: [node.t, node.e, node.n].filter((part) => part !== undefined && part !== "").join(" · "),
        });
        const raw = element("button", { type: "button", className: "instruments-raw-toggle", "aria-expanded": "false", text: "Raw" });
        const nodeHead = element("div", { className: "instruments-node-head" }, [badge, raw, reportLink(doc, node.e)]);
        el.append(nodeHead);
        let rawPre = null;
        raw.addEventListener("click", () => {
          if (!rawPre) {
            rawPre = element("pre", { className: "instruments-raw-text", text: markdown.slice(node.s, node.l) });
            nodeHead.after(rawPre);
          } else {
            rawPre.hidden = !rawPre.hidden;
          }
          raw.setAttribute("aria-expanded", String(!rawPre.hidden));
        });
        // Where this node's element goes: the document, or its parent's children.
        const place = (attach) => {
          if (parentNode === null) documentEl.append(attach);
          else kidsOf.get(parentNode).append(attach);
        };
        if (node.t === "table") {
          // Parsed mode draws a table as an HTML table, one per excerpt (a long table is cut
          // at row boundaries into pieces, each its own excerpt and its own address).
          el.append(tableBody(node));
          nodeEls.set(node.e, el);
          place(el);
          return;
        }
        const text = nodeText(node);
        if (text.trim()) el.append(element("div", { className: "instruments-text" }, [renderText(text)]));
        let kids = null;
        if (node.c && node.c.length) {
          kids = element("div", { className: "instruments-children" });
          el.append(kids);
          kidsOf.set(node, kids);
        }
        nodeEls.set(node.e, el);

        const unit = unitsByEid.get(node.e);
        let attach = el;
        if (unit && unit.u === "heading") {
          // A heading is the container's own first line, not the container.
          nodeHead.append(...tagFor(unit));
          excerptEls.set(excerptId(unit), nodeHead);
        } else if (unit) {
          const wrap = element("div", {
            className: "instruments-excerpt",
            "data-unit-type": unit.u,
            "data-excerpt": excerptId(unit),
          });
          wrap.append(element("div", { className: "instruments-excerpt-bar" }, tagFor(unit)), el);
          if (unit.x) wrap.append(nearDisclosure(unit));
          excerptEls.set(excerptId(unit), wrap);
          attach = wrap;
        }
        place(attach);
      }

      function tableBody(node) {
        const pieces = tableUnits.get(node.e) || [];
        const groups = [];
        for (const block of tableBlocks(markdown, node)) {
          const unit = pieces.find((piece) => piece.s <= block.s && block.s < piece.l) || null;
          const last = groups[groups.length - 1];
          if (last && last.unit === unit) last.blocks.push(block);
          else groups.push({ unit, blocks: [block] });
        }
        const body = element("div", { className: "instruments-table-body" });
        for (const { unit, blocks } of groups) {
          const table = renderTable(blocks);
          if (!unit) {
            body.append(table);
            continue;
          }
          const wrap = element("div", {
            className: "instruments-excerpt",
            "data-unit-type": unit.u,
            "data-excerpt": excerptId(unit),
          });
          wrap.append(element("div", { className: "instruments-excerpt-bar" }, tagFor(unit)), table);
          if (unit.x) wrap.append(nearDisclosure(unit));
          excerptEls.set(excerptId(unit), wrap);
          body.append(wrap);
        }
        return body;
      }

      function renderBatch(limit) {
        const end = Math.min(order.length, cursor + limit);
        for (; cursor < end; cursor++) makeNode(order[cursor][0], order[cursor][1]);
        if (cursor >= order.length && !state.done) {
          state.done = true;
          documentEl.dataset.rendered = "true";
        }
      }

      function frame() {
        if (current && current.state !== state && current.state !== undefined) return; // a newer instrument
        renderBatch(BATCH);
        if (!state.done) requestAnimationFrame(frame);
      }
      state.finish = () => renderBatch(order.length);
      // The first batch is synchronous, so the top of the document exists at once.
      renderBatch(BATCH);
      if (!state.done) requestAnimationFrame(frame);

      // -- navigation inside the instrument ----------------------------------------------- //
      function mark(el) {
        view.querySelectorAll(".instruments-target").forEach((other) => other.classList.remove("instruments-target"));
        el.classList.add("instruments-target");
        el.scrollIntoView({ block: "center" });
      }
      state.mark = mark;
      function jumpToExcerpt(id) {
        state.finish();
        setMode("parsed");
        const el = excerptEls.get(id) || excerptEls.get(id.replace(/\.\d+$/, ""));
        if (el) mark(el);
      }
      function goTo(eId) {
        state.finish();
        setMode("parsed");
        const el = nodeEls.get(eId);
        if (el) mark(el);
      }
      state.jumpToExcerpt = jumpToExcerpt;
      state.goTo = goTo;
      return state;
    }

    // -- the whole document, raw ---------------------------------------------------------- //
    function buildRaw(doc) {
      const markdown = doc.markdown;
      const fragment = document.createDocumentFragment();
      const raws = [];
      (function collect(node) {
        for (const note of node.a || []) raws.push(note.raw);
        for (const child of node.c || []) collect(child);
      })(doc.tree);
      const ranges = doc.units
        .map((unit) => ({ s: unit.s, l: unit.l, unit }))
        .sort((a, b) => a.s - b.s || a.l - b.l);

      function gap(text) {
        if (!text) return;
        if (!text.trim()) {
          fragment.append(text);
          return;
        }
        let rest = text;
        for (const raw of raws) rest = rest.split(raw).join("");
        fragment.append(
          element("span", {
            className: rest.trim() ? "instruments-uncovered" : "instruments-annotation",
            title: rest.trim() ? "Not in any excerpt" : "A reform annotation",
            text,
          })
        );
      }

      let position = 0;
      const front = doc.tree.s || 0;
      if (front > 0) {
        fragment.append(element("span", { className: "instruments-frontmatter", text: markdown.slice(0, front) }));
        position = front;
      }
      for (const range of ranges) {
        if (range.s > position) gap(markdown.slice(position, range.s));
        const start = Math.max(range.s, position);
        if (range.l > start) {
          const id = excerptId(range.unit);
          fragment.append(
            element("span", {
              className: "instruments-range",
              "data-unit-type": range.unit.u,
              "data-excerpt": id,
              "data-label": id,
              title: id,
              text: markdown.slice(start, range.l),
            })
          );
          position = range.l;
        }
      }
      gap(markdown.slice(position));
      return fragment;
    }

    // -- the address ---------------------------------------------------------------------- //
    function parseLocation() {
      const params = new URLSearchParams(location.search);
      let hash = location.hash.replace(/^#/, "");
      try {
        hash = decodeURIComponent(hash);
      } catch (error) {
        // keep it as written
      }
      return { c: params.get("c"), k: params.get("k"), hash };
    }

    function goToHash(hash) {
      if (!hash || !current || !current.state) return;
      const state = current.state;
      state.finish();
      const el = state.excerptEls.get(hash) || state.nodeEls.get(hash) || state.excerptEls.get(hash.replace(/\.\d+$/, ""));
      if (el) state.mark(el);
    }

    // Links to a nearest text are ordinary links: a click stays on the page.
    mount.addEventListener("click", (event) => {
      const link = event.target.closest && event.target.closest("a.instruments-near-link");
      if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.button) return;
      event.preventDefault();
      const target = new URL(link.href, document.baseURI);
      const params = new URLSearchParams(target.search);
      let hash = target.hash.replace(/^#/, "");
      try {
        hash = decodeURIComponent(hash);
      } catch (error) {
        // keep it as written
      }
      openInstrument(params.get("c"), params.get("k"), { push: true, hash });
    });

    window.addEventListener("popstate", () => {
      const where = parseLocation();
      if (where.c && where.k) openInstrument(where.c, where.k, { push: false, hash: where.hash });
      else {
        say("");
        showLanding();
        input.value = "";
      }
    });

    // -- start ------------------------------------------------------------------------------ //
    fillTable();
    const where = parseLocation();
    const finish = () => {
      mount.dataset.ready = "true";
    };
    if (where.c && where.k) {
      openInstrument(where.c, where.k, { push: false, hash: where.hash }).then(() => {
        if (!current) showLanding();
        finish();
      });
    } else {
      showLanding();
      finish();
    }
    mount.dataset.view = mount.dataset.view || "landing";
  }

  fetch(INDEX_URL)
    .then((response) => {
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.json();
    })
    .then(build)
    .catch((error) => {
      mount.replaceChildren(
        element("p", {
          className: "instruments-error",
          text: `The instruments could not be loaded (${error.message}).`,
        })
      );
      mount.dataset.ready = "error";
    });
})();
