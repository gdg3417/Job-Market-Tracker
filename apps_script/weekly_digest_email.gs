const JOB_TRACKER_TIMEZONE = 'America/Chicago';
const DIGEST_SHEET_NAME = 'Digest';
const DASHBOARD_SHEET_NAME = 'Dashboard';
const SECTION_LIMITS = {
  'Immediate review': 10,
  'Strong fit': 10,
  'High-signal titles needing review': 15,
  'Target company watchlist': 10,
  'Needs salary research': 10,
  'Remote or short commute': 10,
  'P&L pathway': 10,
  'New this week': 10,
  'Rejected source audit': 5,
};
const ACTION_SECTIONS = [
  'Immediate review',
  'Strong fit',
  'High-signal titles needing review',
  'Target company watchlist',
  'Needs salary research',
  'Remote or short commute',
  'P&L pathway',
  'New this week',
];

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Job Tracker')
    .addItem('Send test weekly digest', 'sendTestWeeklyDigest')
    .addItem('Send weekly digest now', 'sendWeeklyDigestNow')
    .addToUi();
}

function sendTestWeeklyDigest() {
  sendWeeklyDigest_({ testMode: true });
}

function sendWeeklyDigestNow() {
  sendWeeklyDigest_({ testMode: false });
}

function createMondayMorningWeeklyDigestTrigger() {
  deleteWeeklyDigestTriggers();
  ScriptApp.newTrigger('sendWeeklyDigestNow')
    .timeBased()
    .onWeekDay(ScriptApp.WeekDay.MONDAY)
    .atHour(8)
    .nearMinute(0)
    .inTimezone(JOB_TRACKER_TIMEZONE)
    .create();
}

function deleteWeeklyDigestTriggers() {
  const triggers = ScriptApp.getProjectTriggers();
  triggers.forEach((trigger) => {
    if (trigger.getHandlerFunction() === 'sendWeeklyDigestNow') {
      ScriptApp.deleteTrigger(trigger);
    }
  });
}

function sendWeeklyDigest_(options) {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const digestSheet = spreadsheet.getSheetByName(DIGEST_SHEET_NAME);
  if (!digestSheet) {
    throw new Error('Digest sheet was not found. Run python -m src.dashboard first.');
  }
  const dashboardSheet = spreadsheet.getSheetByName(DASHBOARD_SHEET_NAME);
  const rows = readDigestRows_(digestSheet);
  const grouped = groupRowsBySection_(rows);
  const counts = buildCounts_(grouped);
  const actionableCount = counts['Immediate review'] + counts['Strong fit'] + counts['High-signal titles needing review'] + counts['Target company watchlist'];
  const subject = buildSubject_(actionableCount, options && options.testMode);
  const generated = Utilities.formatDate(new Date(), JOB_TRACKER_TIMEZONE, 'yyyy-MM-dd hh:mm a z');
  const links = {
    spreadsheet: spreadsheet.getUrl(),
    dashboard: sheetUrl_(spreadsheet, dashboardSheet),
    digest: sheetUrl_(spreadsheet, digestSheet),
  };
  const recipient = getDigestRecipient_();
  const textBody = buildTextBody_(generated, counts, grouped, links, actionableCount);
  const htmlBody = buildHtmlBody_(generated, counts, grouped, links, actionableCount);
  MailApp.sendEmail({
    to: recipient,
    subject: subject,
    body: textBody,
    htmlBody: htmlBody,
  });
}

function getDigestRecipient_() {
  const props = PropertiesService.getDocumentProperties();
  const configured = props.getProperty('JOB_TRACKER_DIGEST_RECIPIENT');
  if (configured) {
    return configured;
  }
  const activeUser = Session.getActiveUser().getEmail();
  if (activeUser) {
    return activeUser;
  }
  const effectiveUser = Session.getEffectiveUser().getEmail();
  if (effectiveUser) {
    return effectiveUser;
  }
  throw new Error('No recipient found. Set document property JOB_TRACKER_DIGEST_RECIPIENT to your email address.');
}

function sheetUrl_(spreadsheet, sheet) {
  if (!sheet) {
    return spreadsheet.getUrl();
  }
  return spreadsheet.getUrl() + '#gid=' + sheet.getSheetId();
}

function readDigestRows_(sheet) {
  const values = sheet.getDataRange().getValues();
  const headerIndex = values.findIndex((row) => String(row[0]).trim() === 'digest_section');
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

function groupRowsBySection_(rows) {
  const grouped = {};
  Object.keys(SECTION_LIMITS).forEach((section) => {
    grouped[section] = [];
  });
  const seen = {};
  rows.forEach((row) => {
    const section = String(row.digest_section || '').trim();
    if (!Object.prototype.hasOwnProperty.call(grouped, section)) {
      return;
    }
    const key = [row.company, row.title, row.location, row.canonical_url].join('|').toLowerCase();
    if (section !== 'Rejected source audit') {
      if (seen[key]) {
        return;
      }
      seen[key] = true;
    }
    if (grouped[section].length < SECTION_LIMITS[section]) {
      grouped[section].push(row);
    }
  });
  return grouped;
}

function buildCounts_(grouped) {
  const counts = {};
  Object.keys(SECTION_LIMITS).forEach((section) => {
    counts[section] = grouped[section].length;
  });
  return counts;
}

function buildSubject_(actionableCount, testMode) {
  const prefix = testMode ? 'TEST: ' : '';
  if (actionableCount > 0) {
    return prefix + 'Job Tracker Weekly Digest: ' + actionableCount + ' roles to review';
  }
  return prefix + 'Job Tracker Weekly Digest: No strong fits this week';
}

function buildTextBody_(generated, counts, grouped, links, actionableCount) {
  const lines = [];
  lines.push('Job Tracker Weekly Digest');
  lines.push('Generated: ' + generated);
  lines.push('');
  lines.push('Summary:');
  if (actionableCount > 0) {
    lines.push(actionableCount + ' roles need review.');
    lines.push(counts['Strong fit'] + ' strong fits.');
    lines.push(counts['High-signal titles needing review'] + ' sparse high-signal roles need review.');
    lines.push(counts['Target company watchlist'] + ' target company watchlist roles.');
    lines.push(counts['Needs salary research'] + ' roles need salary research.');
  } else {
    lines.push('No immediate review, strong fit, high-signal review, target company, P&L pathway, or salary research roles were found this week.');
  }
  lines.push('');
  ACTION_SECTIONS.forEach((section) => {
    lines.push(section + ':');
    const rows = grouped[section] || [];
    if (rows.length === 0) {
      lines.push('None');
    } else {
      rows.forEach((row, index) => {
        lines.push((index + 1) + '. ' + value_(row.company) + ' - ' + value_(row.title));
        lines.push('   Location: ' + value_(row.location));
        lines.push('   Score: ' + value_(row.total_score));
        lines.push('   Why it matters: ' + value_(row.score_explanation));
        if (value_(row.canonical_url)) {
          lines.push('   Link: ' + value_(row.canonical_url));
        }
      });
    }
    lines.push('');
  });
  lines.push('Source health:');
  lines.push('Rejected source audit rows: ' + counts['Rejected source audit']);
  lines.push('Most common issue: ' + mostCommonRejection_(grouped['Rejected source audit'] || []));
  lines.push('');
  lines.push('Links:');
  lines.push('Sheet: ' + links.spreadsheet);
  lines.push('Dashboard: ' + links.dashboard);
  lines.push('Digest: ' + links.digest);
  return lines.join('\n');
}

function buildHtmlBody_(generated, counts, grouped, links, actionableCount) {
  const html = [];
  html.push('<h2>Job Tracker Weekly Digest</h2>');
  html.push('<p><b>Generated:</b> ' + escapeHtml_(generated) + '</p>');
  html.push('<h3>Summary</h3>');
  if (actionableCount > 0) {
    html.push('<p>' + actionableCount + ' roles need review.<br>' + counts['Strong fit'] + ' strong fits.<br>' + counts['High-signal titles needing review'] + ' sparse high-signal roles need review.<br>' + counts['Target company watchlist'] + ' target company watchlist roles.<br>' + counts['Needs salary research'] + ' roles need salary research.</p>');
  } else {
    html.push('<p>No immediate review, strong fit, high-signal review, target company, P&amp;L pathway, or salary research roles were found this week.</p>');
  }
  ACTION_SECTIONS.forEach((section) => {
    html.push('<h3>' + escapeHtml_(section) + '</h3>');
    const rows = grouped[section] || [];
    if (rows.length === 0) {
      html.push('<p>None</p>');
      return;
    }
    html.push('<ol>');
    rows.forEach((row) => {
      html.push('<li><b>' + escapeHtml_(value_(row.company)) + ' - ' + escapeHtml_(value_(row.title)) + '</b><br>Location: ' + escapeHtml_(value_(row.location)) + '<br>Score: ' + escapeHtml_(value_(row.total_score)) + '<br>Why it matters: ' + escapeHtml_(value_(row.score_explanation)) + linkHtml_(row.canonical_url) + '</li>');
    });
    html.push('</ol>');
  });
  html.push('<h3>Source health</h3>');
  html.push('<p>Rejected source audit rows: ' + counts['Rejected source audit'] + '<br>Most common issue: ' + escapeHtml_(mostCommonRejection_(grouped['Rejected source audit'] || [])) + '</p>');
  html.push('<h3>Links</h3>');
  html.push('<p><a href="' + escapeHtml_(links.spreadsheet) + '">Google Sheet</a><br><a href="' + escapeHtml_(links.dashboard) + '">Dashboard</a><br><a href="' + escapeHtml_(links.digest) + '">Digest</a></p>');
  return html.join('\n');
}

function mostCommonRejection_(rows) {
  if (!rows || rows.length === 0) {
    return 'None';
  }
  const counts = {};
  rows.forEach((row) => {
    const reason = value_(row.score_explanation) || value_(row.rejection_reason) || 'Unknown';
    counts[reason] = (counts[reason] || 0) + 1;
  });
  let bestReason = 'Unknown';
  let bestCount = 0;
  Object.keys(counts).forEach((reason) => {
    if (counts[reason] > bestCount) {
      bestReason = reason;
      bestCount = counts[reason];
    }
  });
  return bestReason;
}

function linkHtml_(url) {
  const link = value_(url);
  if (!link) {
    return '';
  }
  return '<br><a href="' + escapeHtml_(link) + '">Posting link</a>';
}

function value_(value) {
  if (value === null || value === undefined) {
    return '';
  }
  return String(value).trim();
}

function escapeHtml_(value) {
  return value_(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
