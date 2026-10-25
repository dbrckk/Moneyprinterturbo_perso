"use strict";
// Self-contained offline human review. All displayed benchmark media remain
// local. No fetching, analytics, storage, network, models or inferred votes.
(() => {
  const sections = Array.from(document.querySelectorAll("section[data-case]"));
  const summary = document.getElementById("human-review-summary");
  const button = document.getElementById("export-human-review");
  const csvValue = (value) => '"' + String(value).replace(/"/g, '""') + '"';
  if (!summary || !button || !sections.length) return;
  const choices = new Set(["center", "guided", "tie", "both_bad"]);
  function reviews() {
    return sections.map((section) => {
      const checked = section.querySelector('input[type="radio"]:checked');
      return {
        name: section.getAttribute("data-case") || "",
        choice: checked && choices.has(checked.value) ? checked.value : "",
        note: (section.querySelector("textarea")?.value || "").slice(0, 500),
      };
    });
  }
  function refresh() {
    const entries = reviews();
    const completed = entries.filter((entry) => entry.choice !== "").length;
    summary.textContent = completed + "/" + entries.length +
      " scènes évaluées. Aucune évaluation n'est créée automatiquement.";
    // Partial progress is exportable for the mobile use case.
    button.disabled = completed === 0;
  }
  document.querySelectorAll('input[type="radio"],section[data-case] textarea')
    .forEach((node) => node.addEventListener("change", refresh));
  button.addEventListener("click", () => {
    const entries = reviews();
    if (!entries.some((entry) => entry.choice)) return;
    const lines = ["case,choice,reviewer_notes"];
    for (const entry of entries) {
      lines.push([entry.name, entry.choice, entry.note].map(csvValue).join(","));
    }
    const blob = new Blob(["\uFEFF" + lines.join("\r\n") + "\r\n"],
      {type: "text/csv;charset=utf-8"});
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = "framing-human-review.csv";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 10000);
  });
  refresh();
})();
