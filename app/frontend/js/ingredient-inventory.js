/* A single editable inventory. Canonical aliases come from Python's policy. */
(() => {
  let aliases = {};
  let suggestions = [];
  const basic = name => name.trim().toLowerCase().replace(/[\s_-]+/g, " ");
  const canonical = name => aliases[basic(name)] || basic(name);
  window.IngredientInventory = {
    canonical,
    async load() {
      const response = await fetch("/api/v1/integration/ingredients", {signal: AbortSignal.timeout(8000)});
      if (!response.ok) throw new Error("Ingredient normalisation is unavailable. You can still type ingredients manually.");
      const data = await response.json(); aliases = data.aliases;
      suggestions = data.suggestions || [];
    },
    autocomplete(input, list, onSelect, owned) {
      let matches = [], active = -1;
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-controls", list.id);
      list.setAttribute("role", "listbox");
      const close = () => {
        list.hidden = true; active = -1;
        input.setAttribute("aria-expanded", "false");
        input.removeAttribute("aria-activedescendant");
      };
      const choose = name => {
        onSelect(name); input.value = ""; close(); input.focus();
      };
      const render = () => {
        const query = basic(input.value);
        close(); list.replaceChildren();
        if (!query) return;
        const existing = new Set(owned().map(canonical));
        matches = suggestions.filter(name => !existing.has(canonical(name)) &&
          (name.includes(query) || Object.keys(aliases).some(alias => aliases[alias] === name && alias.includes(query))))
          .sort((a, b) => Number(b.startsWith(query)) - Number(a.startsWith(query)) || a.localeCompare(b)).slice(0, 6);
        matches.forEach((name, index) => {
          const option = document.createElement("button");
          option.type = "button"; option.tabIndex = -1;
          option.id = `${list.id}-${index}`;
          option.setAttribute("role", "option");
          option.setAttribute("aria-selected", "false");
          option.textContent = name;
          option.addEventListener("pointerdown", event => event.preventDefault());
          option.addEventListener("click", () => choose(name));
          list.append(option);
        });
        list.hidden = matches.length === 0;
        input.setAttribute("aria-expanded", String(matches.length > 0));
      };
      input.addEventListener("input", render);
      input.addEventListener("focus", render);
      input.addEventListener("blur", close);
      input.addEventListener("keydown", event => {
        if (event.key === "Escape") { close(); return; }
        if (list.hidden) return;
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          active = (active + (event.key === "ArrowDown" ? 1 : matches.length - 1) + matches.length) % matches.length;
          [...list.children].forEach((option, index) => option.setAttribute("aria-selected", String(index === active)));
          input.setAttribute("aria-activedescendant", list.children[active].id);
        } else if (event.key === "Enter" && active >= 0) {
          event.preventDefault(); choose(matches[active]);
        }
      });
      input.form.addEventListener("submit", close);
      close();
    },
    merge(rows) {
      const merged = new Map();
      rows.forEach(row => {
        const name = canonical(row.name); if (!name) return;
        const previous = merged.get(name);
        if (previous) {
          previous.user_confirmed = previous.user_confirmed || row.user_confirmed;
          previous.needs_confirmation = !previous.user_confirmed && (previous.needs_confirmation || row.needs_confirmation);
        } else merged.set(name, {...row, name});
      });
      return [...merged.values()];
    },
  };
})();
