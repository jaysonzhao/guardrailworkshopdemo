(() => {
  const browser = document.getElementById("rule-browser");
  if (!browser) return;
  const category = document.getElementById("rule-category");
  const rule = document.getElementById("rule-id");
  const groups = [...browser.querySelectorAll(".rule-group")];
  const cards = [...browser.querySelectorAll(".rule-card")];
  function populate() {
    rule.replaceChildren(new Option("All rules in category", ""));
    for (const card of cards) {
      if (!category.value || card.closest(".rule-group").dataset.category === category.value) {
        rule.add(new Option(card.dataset.title + " (" + card.dataset.rule + ")", card.dataset.rule));
      }
    }
  }
  function filter() {
    let visible = 0;
    for (const group of groups) {
      group.hidden = Boolean(category.value && group.dataset.category !== category.value);
      let count = 0;
      for (const card of group.querySelectorAll(".rule-card")) {
        card.hidden = Boolean(rule.value && card.dataset.rule !== rule.value);
        if (rule.value && !card.hidden) card.open = true;
        if (!card.hidden && !group.hidden) { count++; visible++; }
      }
      group.hidden = !count;
    }
    document.getElementById("rule-count").textContent = visible + (visible === 1 ? " rule" : " rules");
  }
  category.addEventListener("change", () => { populate(); filter(); });
  rule.addEventListener("change", filter);
  document.getElementById("rule-reset").addEventListener("click", () => {
    category.value = ""; populate(); filter(); for (const card of cards) card.open = false;
    category.focus();
  });
  populate(); filter(); document.getElementById("rule-filters").hidden = false;
})();
