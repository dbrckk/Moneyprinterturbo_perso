/* Included verbatim inside the self-contained, offline Android review HTML. */
'use strict';
const reviewCases = __REVIEW_DATA__;
const root = document.getElementById('review-cases');
const progress = document.getElementById('progress');
const reveal = document.getElementById('reveal');
const summary = document.getElementById('summary');
const radioNodes = [];


function make(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function choicesFor(changed) {
  return changed
    ? [['left', 'Image de gauche'], ['right', 'Image de droite'],
       ['tie', 'Équivalentes'], ['both_bad', 'Aucune ne convient']]
    : [['tie', 'Même image, pertinente'], ['both_bad', 'Même image, inadaptée']];
}

function csvCell(value) {
  let text = String(value ?? '');
  // Avoid executable spreadsheet formulas when a reviewer opens CSV in Excel.
  if (/^\s*[=+\-@]/.test(text)) text = "'" + text;
  return '"' + text.replace(/"/g, '""') + '"';
}

function buildCSV(rows) {
  const header = ['query', 'baseline_url', 'clip_url',
    'human_choice_baseline_clip_tie_both_bad', 'reviewer_notes'];
  return '\uFEFF' + [header, ...rows].map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

for (const [index, record] of reviewCases.entries()) {
  const section = make('section');
  const heading = make('h2', String(index + 1) + '. ' + record.query);
  section.append(heading);

  const pair = make('div', undefined, 'pair');
  for (const [side, label] of [['left', 'Gauche'], ['right', 'Droite']]) {
    const figure = make('figure');
    const photo = make('img');
    photo.src = record[side];
    photo.alt = 'Illustration ' + label.toLowerCase() + ' : ' + record.query;
    photo.loading = 'lazy';
    const caption = make('figcaption', label);
    figure.append(photo, caption);
    pair.append(figure);
  }
  section.append(pair);
  if (!record.changed) {
    section.append(make('p', 'Les deux côtés présentent la même image. Jugez sa pertinence.'));
  }
  const fieldset = make('fieldset');
  fieldset.setAttribute('aria-label', 'Votre préférence pour ' + record.query);
  for (const [value, title] of choicesFor(record.changed)) {
    const label = make('label', undefined, 'choice');
    const radio = make('input');
    radio.type = 'radio';
    radio.name = 'choice-' + index;
    radio.value = value;
    radio.addEventListener('change', updateProgress);
    radioNodes.push(radio);
    label.append(radio, document.createTextNode(title));
    fieldset.append(label);
  }
  section.append(fieldset);
  const notes = make('textarea', undefined, 'notes');
  notes.rows = 2;
  notes.maxLength = 1000;
  notes.placeholder = 'Observation facultative (ex. sujet coupé, image hors thème…)';
  notes.setAttribute('aria-label', 'Remarques sur ' + record.query);
  notes.id = 'notes-' + index;
  section.append(notes);
  root.append(section);
}

function chosenRows() {
  return reviewCases.map((item, index) => {
    const picked = document.querySelector('input[name="choice-' + index + '"]:checked');
    const value = picked?.value ?? '';
    let choice = value;
    if (value === 'left') choice = item.left_algorithm;
    if (value === 'right') choice = item.left_algorithm === 'clip' ? 'baseline' : 'clip';
    return [item.query, item.baseline_url, item.clip_url, choice,
      document.getElementById('notes-' + index).value];
  });
}

function updateProgress() {
  const count = reviewCases.filter((_, i) =>
    document.querySelector('input[name="choice-' + i + '"]:checked')).length;
  progress.textContent = count + ' / ' + reviewCases.length + ' scènes évaluées';
  reveal.disabled = count !== reviewCases.length || count === 0 || summary.hidden === false;
}
updateProgress();

reveal.addEventListener('click', () => {
  const rows = chosenRows();
  if (!rows.length || rows.some(row => !row[3]) || summary.hidden === false) return;

  const totals = { clip: 0, baseline: 0, tie: 0, both_bad: 0 };
  for (const row of rows) {
    if (!Object.hasOwn(totals, row[3])) return;
    totals[row[3]] += 1;
  }
  const decisive = totals.clip + totals.baseline;
  const share = decisive ? Math.round(100 * totals.clip / decisive) + ' %' : 'non calculable';
  const message = [
    'Bilan des préférences humaines — ' + rows.length + ' scènes',
    'Images choisies par CLIP : ' + totals.clip,
    'Images choisies par le classement classique : ' + totals.baseline,
    'Équivalentes : ' + totals.tie,
    'Les deux inadaptées : ' + totals.both_bad,
    'Part CLIP parmi les préférences décisives : ' + share + ' (' + decisive + ' vote(s)).',
    'Échantillon limité : ce résultat ne mesure pas la justesse objective et ne modifie aucun réglage du générateur.',
    'Votes verrouillés après révélation. Exportez votre CSV ci-dessous.',
  ].join('\n');
  summary.textContent = message;
  summary.hidden = false;
  radioNodes.forEach(radio => { radio.disabled = true; });
  reveal.disabled = true;
  reveal.textContent = 'Bilan affiché · votes verrouillés';
});

document.getElementById('export').addEventListener('click', () => {
  const csv = buildCSV(chosenRows());
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'moneyprinter-human-review.csv';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 15_000);
});
