function sendTestWeeklyContextDigest() {
  sendWeeklyContextOrLegacy_({ testMode: true });
}

function sendWeeklyContextDigestNow() {
  sendWeeklyContextOrLegacy_({ testMode: false });
}

function createMondayMorningWeeklyContextDigestTrigger() {
  deleteWeeklyContextDigestTriggers();
  ScriptApp.newTrigger('sendWeeklyContextDigestNow')
    .timeBased()
    .onWeekDay(ScriptApp.WeekDay.MONDAY)
    .atHour(8)
    .nearMinute(0)
    .inTimezone(JOB_TRACKER_TIMEZONE)
    .create();
}

function deleteWeeklyContextDigestTriggers() {
  const handlers = ['sendWeeklyContextDigestNow', 'sendWeeklyDigestNow'];
  ScriptApp.getProjectTriggers().forEach((trigger) => {
    if (handlers.indexOf(trigger.getHandlerFunction()) >= 0) {
      ScriptApp.deleteTrigger(trigger);
    }
  });
}

function sendWeeklyContextOrLegacy_(options) {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const contextSheet = spreadsheet.getSheetByName(WEEKLY_CONTEXT_SHEET_NAME);
  if (contextSheet && sendWeeklyContextDigest_(spreadsheet, contextSheet, options)) {
    return;
  }
  sendWeeklyDigest_(options);
}

const WEEKLY_CONTEXT_SHEET_NAME = 'Weekly_Context';
const WEEKLY_VALUE_SHEET_NAME = 'Weekly_Value';
const REVIEW_QUEUE_SHEET_NAME = 'Review_Queue';
const FOLLOW_UP_QUEUE_SHEET_NAME = 'Follow_Up_Queue';
const WEEKLY_CONTEXT_SECTION_ORDER = [
  'Action Needed',
  'New Strong Matches',
  'Weekly Tracker Metrics',
  'Backlog and Follow-up',
  'Noise Removed',
];

function sendWeeklyContextDigest_(spreadsheet, contextSheet, options) {
  const rows = readWeeklyContextRows_(contextSheet);
  if (rows.length === 0) {
    return false;
  }
  const grouped = groupWeeklyContextRows_(rows);
  const metrics = weeklyContextMetricMap_(rows);
  const periodRow = rows.find((row) => value_(row.item_type) === 'period') || {};
  const summaryPeriod = value_(periodRow.value) || 'Latest available week';
  const reviewCount = (grouped['Action Needed'] || []).length;
  const followUpCount = (grouped['Backlog and Follow-up'] || []).length;
  const actionCount = reviewCount + followUpCount;
  const subject = buildWeeklyContextSubject_(actionCount, metrics, options && options.testMode);
  const generated = Utilities.formatDate(new Date(), JOB_TRACKER_TIMEZONE, 'yyyy-MM-dd hh:mm a z');
  const links = {
    spreadsheet: spreadsheet.getUrl(),
    weeklyContext: sheetUrl_(spreadsheet, contextSheet),
    weeklyValue: sheetUrl_(spreadsheet, spreadsheet.getSheetByName(WEEKLY_VALUE_SHEET_NAME)),
    reviewQueue: sheetUrl_(spreadsheet, spreadsheet.getSheetByName(REVIEW_QUEUE_SHEET_NAME)),
    followUpQueue: sheetUrl_(spreadsheet, spreadsheet.getSheetByName(FOLLOW_UP_QUEUE_SHEET_NAME)),
  };
  const recipient = getDigestRecipient_();
  MailApp.sendEmail({
    to: recipient,
    subject: subject,
    body: buildWeeklyContextTextBody_(spreadsheet, generated, summaryPeriod, grouped, metrics, links),
    htmlBody: buildWeeklyContextHtmlBody_(spreadsheet, generated, summaryPeriod, grouped, metrics, links),
  });
  return true;
}

function readWeeklyContextRows_(sheet) {
  const values = sheet.getDataRange().getValues();
  const headerIndex = values.findIndex((row) => String(row[0]).trim() === 'section');
  if (headerIndex < 0) {
    return [];
  }
  const headers = values[headerIndex].map((header) => String(header).trim());
  return values.slice(headerIndex + 1)
    .filter((row) => row.some((cell) => cell !== ''))
    .map((row) => {
      const record = {};
      headers.forEach((header, index) => {
        record[header] = row[index];
      });
      return record;
    });
}

function groupWeeklyContextRows_(rows) {
  const grouped = {};
  WEEKLY_CONTEXT_SECTION_ORDER.forEach((section) => {
    grouped[section] = [];
  });
  rows.forEach((row) => {
    const section = value_(row.section);
    if (Object.prototype.hasOwnProperty.call(grouped, section)) {
      grouped[section].push(row);
    }
  });
  return grouped;
}

function weeklyContextMetricMap_(rows) {
  const metrics = {};
  rows.forEach((row) => {
    if (value_(row.item_type) === 'metric') {
      metrics[value_(row.label)] = row.value;
    }
  });
  return metrics;
}

function buildWeeklyContextSubject_(actionCount, metrics, testMode) {
  const prefix = testMode ? 'TEST: ' : '';
  const strong = numericValue_(metrics['Strong Fit Jobs']);
  const stretch = numericValue_(metrics['Stretch Fit Jobs']);
  if (actionCount > 0) {
    return prefix + 'Job Tracker Weekly Context: ' + actionCount + ' action items, ' + (strong + stretch) + ' new fits';
  }
  return prefix + 'Job Tracker Weekly Context: No action needed, ' + (strong + stretch) + ' new fits';
}

function buildWeeklyContextTextBody_(spreadsheet, generated, summaryPeriod, grouped, metrics, links) {
  const lines = [];
  lines.push('Job Tracker Weekly Context');
  lines.push('Summary week: ' + summaryPeriod);
  lines.push('Generated: ' + generated);
  lines.push('');

  appendWeeklyContextTextItems_(lines, spreadsheet, 'Action Needed', grouped['Action Needed'] || []);
  appendWeeklyContextTextItems_(lines, spreadsheet, 'New Strong Matches', grouped['New Strong Matches'] || []);

  lines.push('Weekly Tracker Metrics:');
  appendWeeklyContextTextMetrics_(lines, grouped['Weekly Tracker Metrics'] || []);
  lines.push('');

  appendWeeklyContextTextItems_(lines, spreadsheet, 'Backlog and Follow-up', grouped['Backlog and Follow-up'] || []);

  lines.push('Noise Removed:');
  appendWeeklyContextTextMetrics_(lines, grouped['Noise Removed'] || []);
  lines.push('');

  lines.push('Links:');
  lines.push('Weekly Context: ' + links.weeklyContext);
  lines.push('Weekly Value: ' + links.weeklyValue);
  lines.push('Review Queue: ' + links.reviewQueue);
  lines.push('Follow-Up Queue: ' + links.followUpQueue);
  lines.push('Full Sheet: ' + links.spreadsheet);
  return lines.join('\n');
}

function appendWeeklyContextTextItems_(lines, spreadsheet, heading, rows) {
  lines.push(heading + ':');
  if (rows.length === 0) {
    lines.push('None');
    lines.push('');
    return;
  }
  rows.forEach((row, index) => {
    const fit = value_(row.fit_type) ? ' [' + value_(row.fit_type) + ']' : '';
    lines.push((index + 1) + '. ' + value_(row.company) + ': ' + value_(row.title) + fit);
    if (value_(row.status)) {
      lines.push('   Status: ' + value_(row.status));
    }
    if (value_(row.reason)) {
      lines.push('   Context: ' + value_(row.reason));
    }
    const rowLink = weeklyContextSourceRowUrl_(spreadsheet, row);
    if (rowLink) {
      lines.push('   Tracker row: ' + rowLink);
    }
    if (value_(row.canonical_url)) {
      lines.push('   Posting: ' + value_(row.canonical_url));
    }
  });
  lines.push('');
}

function appendWeeklyContextTextMetrics_(lines, rows) {
  if (rows.length === 0) {
    lines.push('None');
    return;
  }
  rows.forEach((row) => {
    lines.push(value_(row.label) + ': ' + formatWeeklyContextMetric_(row.label, row.value));
  });
}

function buildWeeklyContextHtmlBody_(spreadsheet, generated, summaryPeriod, grouped, metrics, links) {
  const html = [];
  html.push('<h2>Job Tracker Weekly Context</h2>');
  html.push('<p><b>Summary week:</b> ' + escapeHtml_(summaryPeriod) + '<br><b>Generated:</b> ' + escapeHtml_(generated) + '</p>');

  appendWeeklyContextHtmlItems_(html, spreadsheet, 'Action Needed', grouped['Action Needed'] || []);
  appendWeeklyContextHtmlItems_(html, spreadsheet, 'New Strong Matches', grouped['New Strong Matches'] || []);

  html.push('<h3>Weekly Tracker Metrics</h3>');
  appendWeeklyContextHtmlMetrics_(html, grouped['Weekly Tracker Metrics'] || []);

  appendWeeklyContextHtmlItems_(html, spreadsheet, 'Backlog and Follow-up', grouped['Backlog and Follow-up'] || []);

  html.push('<h3>Noise Removed</h3>');
  appendWeeklyContextHtmlMetrics_(html, grouped['Noise Removed'] || []);

  html.push('<h3>Links</h3>');
  html.push(
    '<p><a href="' + escapeHtml_(links.weeklyContext) + '">Weekly Context</a><br>' +
    '<a href="' + escapeHtml_(links.weeklyValue) + '">Weekly Value</a><br>' +
    '<a href="' + escapeHtml_(links.reviewQueue) + '">Review Queue</a><br>' +
    '<a href="' + escapeHtml_(links.followUpQueue) + '">Follow-Up Queue</a><br>' +
    '<a href="' + escapeHtml_(links.spreadsheet) + '">Full Google Sheet</a></p>'
  );
  return html.join('\n');
}

function appendWeeklyContextHtmlItems_(html, spreadsheet, heading, rows) {
  html.push('<h3>' + escapeHtml_(heading) + '</h3>');
  if (rows.length === 0) {
    html.push('<p>None</p>');
    return;
  }
  html.push('<ol>');
  rows.forEach((row) => {
    const fit = value_(row.fit_type) ? ' [' + value_(row.fit_type) + ']' : '';
    const details = [];
    if (value_(row.status)) {
      details.push('<b>Status:</b> ' + escapeHtml_(value_(row.status)));
    }
    if (value_(row.reason)) {
      details.push('<b>Context:</b> ' + escapeHtml_(value_(row.reason)));
    }
    const links = [];
    const rowLink = weeklyContextSourceRowUrl_(spreadsheet, row);
    if (rowLink) {
      links.push('<a href="' + escapeHtml_(rowLink) + '">Tracker row</a>');
    }
    if (value_(row.canonical_url)) {
      links.push('<a href="' + escapeHtml_(value_(row.canonical_url)) + '">Posting</a>');
    }
    html.push(
      '<li><b>' + escapeHtml_(value_(row.company) + ': ' + value_(row.title) + fit) + '</b>' +
      (details.length ? '<br>' + details.join('<br>') : '') +
      (links.length ? '<br>' + links.join(' | ') : '') +
      '</li>'
    );
  });
  html.push('</ol>');
}

function appendWeeklyContextHtmlMetrics_(html, rows) {
  if (rows.length === 0) {
    html.push('<p>None</p>');
    return;
  }
  html.push('<table style="border-collapse:collapse">');
  rows.forEach((row) => {
    html.push(
      '<tr><td style="padding:4px 12px 4px 0">' + escapeHtml_(value_(row.label)) + '</td>' +
      '<td style="padding:4px 0;text-align:right"><b>' + escapeHtml_(formatWeeklyContextMetric_(row.label, row.value)) + '</b></td></tr>'
    );
  });
  html.push('</table>');
}

function weeklyContextSourceRowUrl_(spreadsheet, row) {
  const sheetName = value_(row.source_sheet);
  const rowNumber = parseInt(row.source_row, 10);
  if (!sheetName || !rowNumber) {
    return '';
  }
  const sheet = spreadsheet.getSheetByName(sheetName);
  if (!sheet) {
    return '';
  }
  return spreadsheet.getUrl() + '#gid=' + sheet.getSheetId() + '&range=A' + rowNumber;
}

function formatWeeklyContextMetric_(label, value) {
  const textLabel = value_(label);
  const number = Number(value);
  if (!Number.isNaN(number) && (textLabel.indexOf('Rate') >= 0 || textLabel === 'Signal Quality' || textLabel === 'Noise Removed')) {
    return (number * 100).toFixed(1) + '%';
  }
  return value_(value) || '0';
}

function numericValue_(value) {
  const number = Number(value);
  return Number.isNaN(number) ? 0 : number;
}
