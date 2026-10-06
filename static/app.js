const $ = (selector) => document.querySelector(selector);
const fmt = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n > 1024 ? (n / 1024).toFixed(1) + ' KB' : n + ' B';
const esc = (value) => String(value || '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[char]));
let allFiles = [];

async function refresh() {
  const dashboard = await fetch('/api/dashboard').then((response) => response.json());
  $('#total').textContent = dashboard.total_files;
  $('#storage').textContent = fmt(dashboard.storage_bytes);
  $('#dupes').textContent = dashboard.duplicate_count;
  $('#topicCount').textContent = dashboard.topics.length;
  const max = Math.max(1, ...dashboard.categories.map((item) => item.count));
  $('#categories').innerHTML = dashboard.categories.map((item) =>
    `<div class="cat"><span>${esc(item.category)}</span><div class="bar"><i style="width:${item.count / max * 100}%"></i></div><b>${item.count}</b></div>`
  ).join('') || '<div class="empty">Upload files to see categories.</div>';
  $('#topics').innerHTML = dashboard.topics.map((item) =>
    `<span class="topic">${esc(item.topic)}<b>${item.count}</b></span>`
  ).join('') || '<div class="empty">Topics appear as documents are analyzed.</div>';

  allFiles = await fetch('/api/files').then((response) => response.json());
  renderMyFiles();

  const duplicates = await fetch('/api/duplicates').then((response) => response.json());
  const suggestions = [
    ...duplicates.exact.map((item) => `Exact duplicate: ${esc(item.name)} (matches file #${item.duplicate_of})`),
    ...duplicates.similar.map((item) => `Similar (${Math.round(item.similarity * 100)}%): ${esc(item.file_a)} · ${esc(item.file_b)}`)
  ];
  $('#duplicateList').innerHTML = suggestions.length
    ? suggestions.map((item) => `<div class="search-result">${item}</div>`).join('')
    : '<div class="empty">No duplicates found yet.</div>';
}

function renderMyFiles() {
  const folderSelect = $('#folderFilter');
  const selectedFolder = folderSelect.value;
  const folderCounts = new Map();
  allFiles.forEach((file) => folderCounts.set(file.folder, (folderCounts.get(file.folder) || 0) + 1));
  const folders = [...folderCounts.keys()].sort((a, b) => a.localeCompare(b));
  folderSelect.innerHTML = '<option value="">All folders</option>' + folders.map((folder) =>
    `<option value="${esc(folder)}">${esc(folder)}</option>`
  ).join('');
  folderSelect.value = folders.includes(selectedFolder) ? selectedFolder : '';

  $('#folderCards').innerHTML = folders.map((folder) =>
    `<div class="folder-card${folderSelect.value === folder ? ' selected' : ''}"><button class="folder-open" data-folder="${esc(folder)}"><strong>📁 ${esc(folder)}</strong><small>${folderCounts.get(folder)} file(s) · uploads/${esc(folder)}</small></button><a class="download-button" href="/api/folders/download?folder=${encodeURIComponent(folder)}">Download folder (.zip)</a></div>`
  ).join('') || '<div class="empty">No organized folders yet. Upload files to create them.</div>';
  $('#folderCards').querySelectorAll('[data-folder]').forEach((button) => {
    button.onclick = () => { folderSelect.value = button.dataset.folder; renderMyFiles(); };
  });

  const visibleFiles = folderSelect.value ? allFiles.filter((file) => file.folder === folderSelect.value) : allFiles;
  $('#fileCount').textContent = `${visibleFiles.length} file(s)`;
  $('#fileRows').innerHTML = visibleFiles.map((file) =>
    `<tr><td>${esc(file.original_name)}</td><td><span class="pill">${esc(file.category)}</span></td><td>uploads/${esc(file.folder)}</td><td>${new Date(file.uploaded_at).toLocaleDateString()}</td><td>${fmt(file.size_bytes)}</td><td><a class="download-button" href="/api/files/${file.id}/download">Download</a></td></tr>`
  ).join('') || '<tr><td colspan="6" class="empty">No files in this folder.</td></tr>';
}

const input = $('#fileInput');
const zone = $('#dropzone');
$('#browse').onclick = () => input.click();
input.onchange = () => send(input.files);
zone.ondragover = (event) => { event.preventDefault(); zone.classList.add('drag'); };
zone.ondragleave = () => zone.classList.remove('drag');
zone.ondrop = (event) => { event.preventDefault(); zone.classList.remove('drag'); send(event.dataTransfer.files); };

async function send(files) {
  if (!files.length) return;
  const data = new FormData();
  [...files].forEach((file) => data.append('files', file));
  $('#uploadStatus').textContent = 'Analyzing and organizing…';
  const response = await fetch('/api/upload', { method: 'POST', body: data });
  const payload = await response.json();
  $('#uploadStatus').textContent = payload.error || payload.results.map((item) =>
    item.error ? `${item.name}: ${item.error}` : `Organized ${item.name} → uploads/${item.folder}${item.duplicate ? ' · exact duplicate' : ''}`
  ).join(' | ');
  input.value = '';
  await refresh();
  document.querySelector('#files').scrollIntoView({ behavior: 'smooth' });
}

$('#searchForm').onsubmit = async (event) => {
  event.preventDefault();
  const query = $('#query').value;
  const data = await fetch('/api/search?q=' + encodeURIComponent(query)).then((response) => response.json());
  $('#searchResults').innerHTML = data.results.length ? data.results.map((file) =>
    `<div class="search-result"><b>${esc(file.original_name)}</b><small>${esc(file.category)} · ${esc(file.folder)} · relevance ${file.score}</small>${file.summary ? `<small>${esc(file.summary)}</small>` : ''}<a class="download-button" href="/api/files/${file.id}/download">Download</a></div>`
  ).join('') : '<div class="empty">No matching files. Try a topic, category, or filename.</div>';
};

$('#folderFilter').onchange = renderMyFiles;
$('#refresh').onclick = (event) => { event.preventDefault(); refresh(); };
$('#theme').onclick = () => { document.body.classList.toggle('dark'); localStorage.dark = document.body.classList.contains('dark') ? '1' : '0'; };
if (localStorage.dark === '1') document.body.classList.add('dark');
refresh();
