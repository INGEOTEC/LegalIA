/* The Instruments page (issues #290, #297): every instrument of the Atlas, read one
   by one at the text its map was built from. One IIFE, no library, no build step; it
   reads only the `data-*` attributes of its mount (the index and the folder of
   per-instrument files). The mount also carries `data-repo`, which this script no
   longer uses: the page once linked each node to a prefilled report (issue #290's review
   apparatus, removed in issue #297; see git history at 0f8b5d6) and the attribute stays
   in the mount block because the block is pinned verbatim by the tests. The files are the
   ones scripts/embeddings/export_instrument_files.py writes (issue #289): the raw
   document once, md2akn's tree and its excerpts as UTF-16 offsets into it and, on every
   compared excerpt, its nearest texts. Their parse flags are in the data and are not
   shown.

   The Atlas' combobox is rebuilt here on purpose, not shared: both pages are build-less
   scripts and a shared one would couple the Atlas' pinned script tags to this page. */
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
  // The Mexican term for each node type of md2akn's tree; it names the outline's entries.
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
    hcontainer: "bloque",
    article: "artículo",
    paragraph: "fracción",
    point: "inciso",
    subpoint: "subinciso",
    content: "texto",
  };
  // What an `attachment` is called, from its `refersTo` and never from its eId: md2akn
  // types an annex and an appendix alike (Akoma Ntoso's `attachment`).
  const ATTACHMENT_NAMES = { "#anexo": "Annex", "#apendice": "Appendix" };
  // What an `hcontainer` is, from its `refersTo` (md2akn's rules 29 to 31): the Mexican
  // promulgation decree's blocks grouped, a note, a closing formula, a model certificate or
  // form, a division a restarted numbering opened, and any other block the Court labels
  // (named by its own label).
  const HCONTAINER_NAMES = {
    "#promulgation": "Promulgation decree",
    "#note": "Note",
    "#conclusions": "Closing formulas",
    "#form": "Form",
    "#division": "Division",
  };
  const CONTAINERS = new Set(["book", "title", "chapter", "section", "level", "part", "attachment", "hcontainer"]);
  const SUGGESTIONS = 8;
  const PAGE_SIZE = 50;
  const BATCH = 300;
  const NOT_AVAILABLE = "This instrument's file is not available.";
  const OTHER_VERSION = "This instrument's file was built from a different version of the data.";

  const mount = document.getElementById("instruments");
  if (!mount) return;

  const INDEX_URL = new URL(mount.dataset.index || "instruments/instruments.json", document.baseURI);
  const FILES_URL = new URL(mount.dataset.files || "instruments/files/", document.baseURI);

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

  // The SCJN's outline marker lines (`<!-- scjn:block orden=4 numero=0 kind=other
  // label="..." -->`, one before each API block) are metadata for md2akn and never text for
  // a reader. This expression is a copy of `scjn.outline.MARKER` on purpose: the page is
  // build-less and reads the files as published, so a change to the marker grammar is a
  // change here too. A line that is a marker goes, and the line break after it. It is
  // display only: the `s`/`l` offsets and `data-start`/`data-end` are never adjusted.
  const MARKER_LINE = /^<!-- scjn:block .*? -->[ \t]*$(?:\r?\n)?/gm;

  function stripMarkers(text) {
    return String(text || "").replace(MARKER_LINE, "");
  }

  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"];

  // `14-11-2025` or `14-11-2025.md` -> { time, text: "14 November 2025" }; anything else is
  // shown as written and sorts first.
  function readDate(value) {
    const found = /^(\d{2})-(\d{2})-(\d{4})/.exec(String(value || ""));
    if (!found) return { time: 0, text: String(value || "") };
    const [, day, month, year] = found;
    const name = MONTHS[Number(month) - 1];
    return {
      time: Date.UTC(Number(year), Number(month) - 1, Number(day)),
      text: name ? `${Number(day)} ${name} ${year}` : String(value),
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
    stripMarkers(text)
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
      // A block that is only a marker line is nothing to draw.
      const body = stripMarkers(raw).trim();
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
    // The blocks before the first row are the table's caption (`**A.** Tarifa para ...`,
    // `**TARIFA**`, md2akn's rule 36): a `<caption>`, not rows of the table.
    let first = 0;
    while (first < blocks.length && !rows[first]) first++;
    const caption = first > 0 && first < blocks.length
      ? element("caption", { className: "instruments-table-caption" },
          blocks.slice(0, first).map((block) => appendInline(element("span", { className: "instruments-table-caption-line" }), block.text)))
      : null;
    blocks.forEach((block, n) => {
      if (n < first && caption) return;
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
      element("table", { className: "instruments-table" }, caption ? [caption, body] : [body]),
    ]);
  }

  // The `<tr>` of one table row, bold kept.
  function rowElement(cells) {
    const tr = element("tr", { className: "instruments-table-row" });
    cells.forEach((cell) => tr.append(appendInline(element("td"), cell)));
    return tr;
  }

  // A table inside an article is not a node of md2akn's tree (the article is one
  // excerpt, whatever it holds): each of its rows is a `content` leaf of the article.
  // The page draws the run of such leaves as one HTML table, for display only -- the
  // excerpt and its nearest texts are the article's, unchanged.
  function appendRowTo(host, node, text) {
    let group = host.lastElementChild;
    if (!group || !group.classList.contains("instruments-table-group")) {
      group = element("section", {
        className: "instruments-node instruments-type-table-group instruments-table-group",
        "data-type": "table",
        "data-eid": node.e,
        "data-eids": node.e,
        "data-start": String(node.s),
      }, [element("div", { className: "instruments-table-scroll" }, [
        element("table", { className: "instruments-table" }, [element("tbody")]),
      ])]);
      host.append(group);
    } else {
      group.dataset.eids += ` ${node.e}`;
    }
    group.dataset.end = String(node.l);
    group.querySelector("tbody").append(rowElement(tableCells(text)));
    return group;
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
    else label = "Text";
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
      date: readDate(d.archivo),
    }));
    for (const item of items) {
      if (!COLLECTIONS[item.c]) {
        throw new Error(`the index has a collection this page does not know: ${item.c}`);
      }
    }
    const byKey = new Map(items.map((item) => [`${item.c}/${item.k}`, item]));
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
      { key: "c", label: "Collection", text: true },
      { key: "t", label: "Text as of" },
      { key: "a", label: "Articles" },
    ];
    let sortKey = "n";
    let sortDirection = 1;
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
      text: `${number(meta.instruments)} instruments. Click a column to sort; a name opens the instrument.`,
    });
    const head = element("tr");
    const headerButtons = COLUMNS.map((column) => {
      const button = element("button", { type: "button", className: "instruments-sort", text: column.label });
      const cell = element("th", { scope: "col", "data-key": column.key, "aria-sort": "none" }, [button]);
      if (!column.text) cell.className = "instruments-number";
      else if (column.key === "c") cell.className = "instruments-kind";
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
      if (key === "c") return COLLECTIONS[item.c].one;
      if (key === "t") return item.date.time;
      return item.d[key];
    }

    function fillTable() {
      const rows = items.filter((item) => !filter || item.c === filter);
      rows.sort((a, b) => {
        const x = valueOf(a, sortKey);
        const y = valueOf(b, sortKey);
        const order =
          typeof x === "string" ? x.localeCompare(y) * sortDirection : (x - y) * sortDirection;
        return order || a.i - b.i;
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
          element("td", { className: "instruments-kind", text: COLLECTIONS[item.c].one }),
          element("td", { className: "instruments-number", text: item.date.text }),
          element("td", { className: "instruments-number", text: number(item.d.a) }),
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
        // then an abbreviation or id. Among names of one kind the one the query
        // covers most of comes first (the Atlas page ranks the same way).
        const rank = item.folded.startsWith(folded) || item.foldedKey === folded ? 0 : inName ? 1 : 2;
        const coverage = rank === 2 ? 0 : folded.length / item.folded.length;
        found.push({ item, rank, coverage });
      }
      found.sort((a, b) => a.rank - b.rank || b.coverage - a.coverage || b.item.d.a - a.item.d.a
        || a.item.i - b.item.i);
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
      for (const unit of doc.units) {
        unitsByExcerpt.set(excerptId(unit), unit);
        if (unit.u === "table") {
          if (!tableUnits.has(unit.e)) tableUnits.set(unit.e, []);
          tableUnits.get(unit.e).push(unit);
        }
        // A heading and the text under it share an eId when a container carries
        // both; the first excerpt of a node (piece 0) is the one the node shows.
        if (!unitsByEid.has(unit.e)) unitsByEid.set(unit.e, unit);
      }
      const excerptEls = new Map(); // excerpt id -> the element to scroll to
      const nodeEls = new Map(); // eId -> the node element
      const state = { excerptEls, nodeEls, done: false };

      // -- header ----------------------------------------------------------------- //
      const info = COLLECTIONS[m.c];
      const identifier =
        m.c === "leyes"
          ? ["Abbreviation", m.k]
          : ["SCJN id", m.k];
      const back = element("button", { type: "button", className: "instruments-back", text: "← All instruments" });
      back.addEventListener("click", backToTable);
      const when = readDate(m.fecha_publicacion);
      const header = element("header", { className: "instruments-head" }, [
        back,
        element("div", { className: "instruments-head-line" }, [
          element(
            "span",
            { className: "instruments-collection", style: `background:${info.color}`, text: info.one }
          ),
        ]),
        element("h2", { className: "instruments-title", text: m.n }),
        element("p", { className: "instruments-facts" }, [
          `${identifier[0]} `,
          element("strong", { text: m.k }),
          ` · text as of ${when.text} · ${number(item.d.a)} articles`,
        ]),
      ]);

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
      const reading = element("div", { className: "instruments-reading" }, [documentEl]);
      const layout = element("div", { className: "instruments-layout" }, [outline, reading]);
      view.append(header, layout);

      // the outline
      function outlineLabel(node) {
        if (node.t === "intro") return "Introduction";
        if (node.t === "table") return `Table ${node.n || ""}`.trim();
        if (node.t === "hcontainer") {
          // The group of decree blocks needs no label (its name says it); every other
          // one is named by what it is and by the Court's own label, when it has one.
          if (node.r === "#promulgation") return HCONTAINER_NAMES[node.r];
          const kind = HCONTAINER_NAMES[node.r] || "Block";
          return node.n ? `${kind}: ${node.n}` : kind;
        }
        const term = TERMS[node.t] || node.t;
        const name =
          node.t === "attachment" && ATTACHMENT_NAMES[node.r]
            ? ATTACHMENT_NAMES[node.r]
            : node.r === "#transitorios"
              ? "Transitory section"
              : term.charAt(0).toUpperCase() + term.slice(1);
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
        if (node.t === "article" || node.t === "intro" || node.t === "table") {
          go.textContent = node.t !== "article" ? outlineLabel(node) : node.n ? `Art. ${node.n}` : "Article";
        } else {
          go.textContent = outlineLabel(node);
        }
        go.setAttribute("title", node.h ? `${outlineLabel(node)} — ${node.h}` : outlineLabel(node));
        go.addEventListener("click", () => {
          goTo(node.e);
          outline.dataset.open = "false";
          outlineToggle.setAttribute("aria-expanded", "false");
        });
        li.append(go);
        const children = node.c || [];
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
                  title: excerptLabel(targetEid, targetPiece),
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

      // A container's own first line is a heading: `<h3>` for a child of the instrument, one
      // level deeper per container above it, `<h6>` at most. `**bold**` is kept, a line
      // break between lines of the heading is a line break, nothing else is read.
      function headingFor(text, level) {
        const heading = element("h" + String(level), { className: "instruments-heading" });
        text
          .split(/\n/)
          .map((line) => line.trim())
          .filter(Boolean)
          .forEach((line, n) => {
            if (n) heading.append(element("br"));
            appendInline(heading, line);
          });
        return heading;
      }

      // The label the Court gives a block that has no heading line of its own in the text (a
      // preamble, `APROBACIÓN DE INSTRUMENTOS`, `PARTE FINAL DEL ACTA`): drawn as a heading at the
      // node's depth, in an editorial style, so the reader sees where the Court cut the instrument.
      // Review fix-1 of #301.
      const LABEL_TITLE = "Section label from the SCJN's database";
      function foldLabel(text) {
        return String(text || "")
          .normalize("NFD")
          .replace(/[\u0300-\u036f]/g, "")
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "");
      }
      function firstLineOf(node) {
        const lines = stripMarkers(markdown.slice(node.s, node.l)).split(/\n/);
        const line = lines.find((candidate) => candidate.trim());
        return line === undefined ? "" : line;
      }
      function labelHeading(label, level) {
        return element("h" + String(level), {
          className: "instruments-heading instruments-label",
          title: LABEL_TITLE,
          text: label,
        });
      }

      const depthOf = new Map(); // a node -> how many containers hold it
      function containerDepth(node, parentNode) {
        const depth = parentNode === null ? 0 : (depthOf.get(parentNode) || 0) + (CONTAINERS.has(parentNode.t) ? 1 : 0);
        depthOf.set(node, depth);
        return depth;
      }

      // An excerpt: the text the Atlas holds one vector for. A compared one is marked and
      // lists its nearest texts; any other is only addressable (`#<eId>` scrolls to it).
      function excerptWrap(unit, content) {
        const wrap = element("div", { className: "instruments-excerpt", "data-excerpt": excerptId(unit) });
        wrap.append(content);
        if (unit.x) {
          wrap.dataset.compared = "true";
          wrap.append(nearDisclosure(unit));
        }
        excerptEls.set(excerptId(unit), wrap);
        return wrap;
      }

      function makeNode(node, parentNode) {
        const el = element("section", {
          className: `instruments-node instruments-type-${node.t}`,
          "data-eid": node.e,
          "data-type": node.t,
          "data-start": String(node.s),
          "data-end": String(node.l),
        });
        const depth = containerDepth(node, parentNode);
        // Where this node's element goes: the document, or its parent's children.
        const place = (attach) => {
          if (parentNode === null) documentEl.append(attach);
          else kidsOf.get(parentNode).append(attach);
        };
        if (node.t === "table") {
          // A table node is drawn as an HTML table, one per excerpt (a long table is cut
          // at row boundaries into pieces, each its own excerpt and its own address).
          el.append(tableBody(node));
          nodeEls.set(node.e, el);
          place(el);
          return;
        }
        if (node.t === "content" && !(node.c && node.c.length)) {
          // A row of a table printed inside an article joins the table the row before it opened.
          const body = stripMarkers(nodeText(node)).trim();
          const host = parentNode === null ? documentEl : kidsOf.get(parentNode);
          if (host && IS_ROW.test(body)) {
            nodeEls.set(node.e, appendRowTo(host, node, body));
            return;
          }
        }
        const unit = unitsByEid.get(node.e);
        let text = stripMarkers(nodeText(node));
        if (unit && unit.u === "heading") {
          // The heading unit is the container's own first line, not the container; whatever
          // the node holds after it is ordinary text.
          const own = stripMarkers(markdown.slice(unit.s, unit.l));
          const at = text.indexOf(own.trim());
          const level = Math.min(6, 3 + depth);
          const heading = headingFor(own, level);
          el.append(heading);
          excerptEls.set(excerptId(unit), heading);
          if (at >= 0) text = text.slice(at + own.trim().length);
        }
        if (
          CONTAINERS.has(node.t) &&
          node.n &&
          node.r !== "#promulgation" &&
          !(unit && unit.u === "heading") &&
          !text.trim() &&
          foldLabel(firstLineOf(node)) !== foldLabel(node.n)
        ) {
          el.append(labelHeading(node.n, Math.min(6, 3 + depth)));
        }
        if (text.trim()) el.append(element("div", { className: "instruments-text" }, [renderText(text)]));
        if (node.c && node.c.length) {
          const kids = element("div", { className: "instruments-children" });
          el.append(kids);
          kidsOf.set(node, kids);
        }
        nodeEls.set(node.e, el);
        place(unit && unit.u !== "heading" ? excerptWrap(unit, el) : el);
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
          body.append(excerptWrap(unit, table));
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
        // The start of the target goes to the top of the viewport. Centring it (the review
        // page's choice, where the target was a badge) leaves the middle of an article taller
        // than the screen in view and the reader cannot tell where it begins (review fix-1 of #297).
        el.scrollIntoView({ block: "start" });
      }
      state.mark = mark;
      function goTo(eId) {
        state.finish();
        const el = nodeEls.get(eId);
        if (el) mark(el);
      }
      state.goTo = goTo;
      return state;
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
