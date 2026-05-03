"""
Flask UI для просмотра и скачивания результатов регистрации checko.ru
"""

import csv
import io
import os
import threading

from flask import Flask, Response, render_template_string, jsonify

app = Flask(__name__)

OUTPUT_CSV = os.environ.get("OUTPUT_CSV", "checko_accounts.csv")

HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Checko Accounts</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0f1117;
      color: #e2e8f0;
      min-height: 100vh;
      padding: 32px 24px;
    }

    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 24px;
      flex-wrap: wrap;
      gap: 12px;
    }

    h1 {
      font-size: 1.4rem;
      font-weight: 600;
      color: #f8fafc;
    }

    .badge {
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 6px;
      padding: 4px 12px;
      font-size: 0.8rem;
      color: #94a3b8;
    }

    .actions {
      display: flex;
      gap: 10px;
      align-items: center;
    }

    .btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 16px;
      border-radius: 8px;
      font-size: 0.875rem;
      font-weight: 500;
      cursor: pointer;
      border: none;
      text-decoration: none;
      transition: opacity 0.15s;
    }
    .btn:hover { opacity: 0.85; }

    .btn-primary {
      background: #3b82f6;
      color: #fff;
    }

    .btn-secondary {
      background: #1e293b;
      color: #94a3b8;
      border: 1px solid #334155;
    }

    .status {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 0.8rem;
      color: #64748b;
    }

    .dot {
      width: 8px; height: 8px;
      border-radius: 50%;
      background: #64748b;
    }
    .dot.running { background: #22c55e; animation: pulse 1.2s infinite; }
    .dot.done    { background: #3b82f6; }

    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50%       { opacity: 0.4; }
    }

    .table-wrap {
      overflow-x: auto;
      border-radius: 10px;
      border: 1px solid #1e293b;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.875rem;
    }

    thead tr {
      background: #1e293b;
    }

    th {
      padding: 12px 16px;
      text-align: left;
      font-weight: 600;
      color: #94a3b8;
      text-transform: uppercase;
      font-size: 0.75rem;
      letter-spacing: 0.05em;
      white-space: nowrap;
    }

    tbody tr {
      border-top: 1px solid #1e293b;
      transition: background 0.1s;
    }
    tbody tr:hover { background: #1e293b55; }

    td {
      padding: 11px 16px;
      color: #cbd5e1;
      font-family: "JetBrains Mono", "Fira Code", monospace;
      font-size: 0.82rem;
      white-space: nowrap;
    }

    td.login  { color: #f8fafc; }
    td.apikey { color: #7dd3fc; }

    .copy-btn {
      background: none;
      border: 1px solid #334155;
      border-radius: 4px;
      color: #64748b;
      padding: 2px 8px;
      font-size: 0.7rem;
      cursor: pointer;
      margin-left: 8px;
      transition: all 0.15s;
    }
    .copy-btn:hover { border-color: #3b82f6; color: #3b82f6; }
    .copy-btn.copied { border-color: #22c55e; color: #22c55e; }

    .empty {
      text-align: center;
      padding: 60px 20px;
      color: #475569;
    }
    .empty svg { margin-bottom: 12px; opacity: 0.4; }

    #progress-bar-wrap {
      height: 3px;
      background: #1e293b;
      border-radius: 2px;
      margin-bottom: 20px;
      overflow: hidden;
    }
    #progress-bar {
      height: 100%;
      background: #3b82f6;
      width: 0%;
      transition: width 0.4s ease;
    }
  </style>
</head>
<body>

<div class="header">
  <div style="display:flex;align-items:center;gap:12px;">
    <h1>Checko Accounts</h1>
    <span class="badge" id="count-badge">0 аккаунтов</span>
  </div>
  <div class="actions">
    <span class="status">
      <span class="dot" id="status-dot"></span>
      <span id="status-text">Загрузка...</span>
    </span>
    <button class="btn btn-secondary" onclick="loadData()">↻ Обновить</button>
    <a class="btn btn-primary" href="/download">⬇ Скачать CSV</a>
  </div>
</div>

<div id="progress-bar-wrap" style="display:none">
  <div id="progress-bar"></div>
</div>

<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>Login (Email)</th>
        <th>Password</th>
        <th>API Key</th>
      </tr>
    </thead>
    <tbody id="tbody">
      <tr><td colspan="4" class="empty">
        <svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414A1 1 0 0119 9.414V19a2 2 0 01-2 2z"/>
        </svg>
        <div>Данных пока нет — запусти register_checko.py</div>
      </td></tr>
    </tbody>
  </table>
</div>

<script>
  let autoRefresh = null;

  function copyText(text, btn) {
    navigator.clipboard.writeText(text).then(() => {
      btn.textContent = "✓";
      btn.classList.add("copied");
      setTimeout(() => { btn.textContent = "copy"; btn.classList.remove("copied"); }, 1500);
    });
  }

  async function loadData() {
    const res = await fetch("/api/accounts");
    const data = await res.json();

    const tbody = document.getElementById("tbody");
    const badge = document.getElementById("count-badge");
    const dot   = document.getElementById("status-dot");
    const stxt  = document.getElementById("status-text");
    const pbWrap = document.getElementById("progress-bar-wrap");
    const pb     = document.getElementById("progress-bar");

    badge.textContent = data.rows.length + " аккаунтов";

    // Статус
    if (data.running) {
      dot.className = "dot running";
      stxt.textContent = "Регистрация идёт...";
      pbWrap.style.display = "block";
      const pct = data.total > 0 ? (data.rows.length / data.total * 100) : 0;
      pb.style.width = pct + "%";
      if (!autoRefresh) autoRefresh = setInterval(loadData, 3000);
    } else {
      dot.className = "dot done";
      stxt.textContent = data.rows.length > 0 ? "Готово" : "Ожидание запуска";
      pbWrap.style.display = "none";
      if (autoRefresh) { clearInterval(autoRefresh); autoRefresh = null; }
    }

    if (data.rows.length === 0) {
      tbody.innerHTML = `<tr><td colspan="4" class="empty">
        <svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414A1 1 0 0119 9.414V19a2 2 0 01-2 2z"/>
        </svg>
        <div>Данных пока нет — запусти register_checko.py</div>
      </td></tr>`;
      return;
    }

    tbody.innerHTML = data.rows.map((r, i) => `
      <tr>
        <td style="color:#475569">${i + 1}</td>
        <td class="login">${r.login}
          <button class="copy-btn" onclick="copyText('${r.login}', this)">copy</button>
        </td>
        <td>${r.password}
          <button class="copy-btn" onclick="copyText('${r.password}', this)">copy</button>
        </td>
        <td class="apikey">${r.api_key}
          <button class="copy-btn" onclick="copyText('${r.api_key}', this)">copy</button>
        </td>
      </tr>
    `).join("");
  }

  loadData();
</script>
</body>
</html>
"""

def read_status() -> tuple[bool, int]:
    """Читает .status файл который пишет register_checko.py."""
    if not os.path.exists(".status"):
        return False, 0
    try:
        parts = open(".status").read().strip().split("|")
        running = parts[0] == "True"
        total   = int(parts[1]) if len(parts) > 1 else 0
        return running, total
    except Exception:
        return False, 0


def read_csv() -> list[dict]:
    if not os.path.exists(OUTPUT_CSV):
        return []
    rows = []
    with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="|")
        for row in reader:
            rows.append({
                "login":    row.get("login", "").strip(),
                "password": row.get("password", "").strip(),
                "api_key":  row.get("api_key", "").strip(),
            })
    return rows


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/accounts")
def api_accounts():
    rows = read_csv()
    running, total = read_status()
    return jsonify({
        "rows":    rows,
        "running": running,
        "total":   total,
    })


@app.route("/download")
def download():
    rows = read_csv()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["login", "password", "api_key"], delimiter="|")
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = output.getvalue().encode("utf-8")

    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=checko_accounts.csv"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
