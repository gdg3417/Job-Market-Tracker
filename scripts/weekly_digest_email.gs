/**
 * Optional Sprint 11 helper.
 *
 * Install this in the bound Apps Script project for the Job Market Tracker Google Sheet.
 * Set RECIPIENT_EMAIL before running or creating a weekly trigger.
 */
const RECIPIENT_EMAIL = 'replace_me@example.com';
const DIGEST_SHEET_NAME = 'Digest';

function emailWeeklyJobTrackerDigest() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const digestSheet = spreadsheet.getSheetByName(DIGEST_SHEET_NAME);
  if (!digestSheet) {
    throw new Error(`Missing sheet: ${DIGEST_SHEET_NAME}`);
  }

  const values = digestSheet.getDataRange().getDisplayValues();
  if (values.length < 5) {
    throw new Error('Digest sheet does not contain the expected Sprint 11 layout.');
  }

  const generatedAt = values[1] && values[1][1] ? values[1][1] : new Date().toLocaleString();
  const headers = values[4];
  const rows = values.slice(5).filter(row => row.some(cell => String(cell).trim() !== ''));
  const sectionCounts = countSections_(rows);

  const subject = `Job Market Tracker Weekly Digest: ${rows.length} roles`;
  const htmlBody = buildDigestHtml_(spreadsheet.getUrl(), generatedAt, headers, rows, sectionCounts);
  const plainBody = buildDigestPlainText_(spreadsheet.getUrl(), generatedAt, rows, sectionCounts);

  MailApp.sendEmail({
    to: RECIPIENT_EMAIL,
    subject,
    body: plainBody,
    htmlBody,
  });
}

function countSections_(rows) {
  const counts = {};
  rows.forEach(row => {
    const section = row[0] || 'Unsectioned';
    counts[section] = (counts[section] || 0) + 1;
  });
  return counts;
}

function buildDigestPlainText_(spreadsheetUrl, generatedAt, rows, sectionCounts) {
  const sectionLines = Object.keys(sectionCounts)
    .map(section => `${section}: ${sectionCounts[section]}`)
    .join('\n');

  const topRows = rows.slice(0, 20).map(row => {
    const section = row[0];
    const company = row[1];
    const title = row[2];
    const location = row[3];
    const score = row[9];
    const url = row[17];
    return `${section}: ${company} | ${title} | ${location} | score ${score}\n${url}`;
  }).join('\n\n');

  return [
    `Generated at: ${generatedAt}`,
    '',
    'Section counts:',
    sectionLines || 'No digest rows.',
    '',
    'Top rows:',
    topRows || 'No digest rows.',
    '',
    `Open tracker: ${spreadsheetUrl}`,
  ].join('\n');
}

function buildDigestHtml_(spreadsheetUrl, generatedAt, headers, rows, sectionCounts) {
  const sectionHtml = Object.keys(sectionCounts)
    .map(section => `<li><strong>${escapeHtml_(section)}</strong>: ${sectionCounts[section]}</li>`)
    .join('');

  const tableRows = rows.slice(0, 50).map(row => {
    const cells = [0, 1, 2, 3, 7, 8, 9, 10, 11, 12, 14, 17].map(index => {
      const value = row[index] || '';
      if (index === 17 && value) {
        return `<td><a href="${escapeHtml_(value)}">Open</a></td>`;
      }
      return `<td>${escapeHtml_(value)}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');

  const selectedHeaders = [
    'Section',
    'Company',
    'Title',
    'Location',
    'Role Family',
    'Role Level',
    'Score',
    'Alert Tier',
    'Salary Min',
    'Salary Max',
    'Days Open',
    'URL',
  ];

  return `
    <div>
      <p><strong>Generated at:</strong> ${escapeHtml_(generatedAt)}</p>
      <p><a href="${escapeHtml_(spreadsheetUrl)}">Open Job Market Tracker</a></p>
      <h3>Section counts</h3>
      <ul>${sectionHtml || '<li>No digest rows.</li>'}</ul>
      <h3>Digest rows</h3>
      <table border="1" cellpadding="6" cellspacing="0">
        <thead><tr>${selectedHeaders.map(header => `<th>${escapeHtml_(header)}</th>`).join('')}</tr></thead>
        <tbody>${tableRows || '<tr><td colspan="12">No digest rows.</td></tr>'}</tbody>
      </table>
    </div>
  `;
}

function escapeHtml_(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
