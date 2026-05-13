"""Simple observability web server for queue metrics."""

from __future__ import annotations

import json
import logging
from typing import Any

try:
    from aiohttp import web
except ModuleNotFoundError:
    raise RuntimeError(
        "aiohttp is required for observability. "
        "Install with: pip install aiohttp"
    )

from metrics import MetricsCollector

log = logging.getLogger("violet.observability")


HTML_DASHBOARD = """
<!DOCTYPE html>
<html>
<head>
    <title>Violet Queue Metrics</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: #0f0f0f;
            color: #e0e0e0;
            padding: 20px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        h1 {
            margin-bottom: 20px;
            font-size: 2em;
            color: #fff;
        }
        .summary {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: #1a1a1a;
            padding: 20px;
            border-radius: 8px;
            border-left: 4px solid #6366f1;
        }
        .stat-label {
            font-size: 0.85em;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }
        .stat-value {
            font-size: 2em;
            font-weight: bold;
            color: #fff;
        }
        .queues {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(450px, 1fr));
            gap: 20px;
        }
        .queue-card {
            background: #1a1a1a;
            border-radius: 8px;
            padding: 20px;
            border: 1px solid #2a2a2a;
        }
        .queue-card.active {
            border-color: #6366f1;
            background: #1a1a2e;
        }
        .queue-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            padding-bottom: 15px;
            border-bottom: 1px solid #2a2a2a;
        }
        .queue-id {
            font-weight: bold;
            font-size: 1.1em;
            color: #fff;
            word-break: break-all;
        }
        .queue-status {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        .badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.75em;
            font-weight: bold;
            text-transform: uppercase;
        }
        .badge.active {
            background: #4ade80;
            color: #000;
        }
        .badge.idle {
            background: #64748b;
            color: #fff;
        }
        .metric {
            margin-bottom: 10px;
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
        }
        .metric-label {
            color: #888;
            font-size: 0.9em;
        }
        .metric-value {
            font-weight: bold;
            color: #fff;
        }
        .messages-section {
            margin-top: 15px;
        }
        .messages-title {
            font-size: 0.9em;
            color: #666;
            text-transform: uppercase;
            margin-bottom: 10px;
            font-weight: bold;
        }
        .message-card {
            background: #0f0f0f;
            border: 1px solid #2a2a2a;
            border-radius: 6px;
            padding: 12px;
            margin-bottom: 10px;
            position: relative;
        }
        .message-card.processing {
            border-color: #f59e0b;
            background: #1f2937;
        }
        .message-card.queued {
            border-color: #3b82f6;
        }
        .message-position {
            position: absolute;
            top: -12px;
            left: 12px;
            background: #0f0f0f;
            padding: 0 6px;
            font-size: 0.75em;
            color: #888;
            font-weight: bold;
        }
        .message-card.processing .message-position {
            background: #1f2937;
            color: #f59e0b;
        }
        .message-header {
            display: flex;
            gap: 10px;
            align-items: flex-start;
            margin-bottom: 8px;
        }
        .message-avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: #2a2a2a;
            flex-shrink: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.75em;
            color: #888;
            overflow: hidden;
        }
        .message-avatar img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        .message-info {
            flex: 1;
            min-width: 0;
        }
        .message-author {
            font-weight: bold;
            color: #fff;
            font-size: 0.95em;
        }
        .message-meta {
            font-size: 0.75em;
            color: #666;
            margin-top: 2px;
        }
        .message-content {
            color: #e0e0e0;
            font-size: 0.9em;
            word-break: break-word;
            margin-top: 8px;
            padding: 8px;
            background: #0a0a0a;
            border-radius: 4px;
            border-left: 2px solid #3b82f6;
            font-family: "Monaco", "Courier New", monospace;
            max-height: 60px;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .message-card.processing .message-content {
            border-left-color: #f59e0b;
        }
        .processing-indicator {
            font-size: 0.75em;
            color: #f59e0b;
            font-weight: bold;
            margin-top: 8px;
        }
        .refresh-info {
            text-align: center;
            color: #666;
            margin-top: 30px;
            font-size: 0.85em;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎵 Violet Queue Metrics</h1>
        
        <div class="summary" id="summary">
            <div class="stat-card">
                <div class="stat-label">Contexts</div>
                <div class="stat-value" id="stat-contexts">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Active Workers</div>
                <div class="stat-value" id="stat-workers">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total Queue Depth</div>
                <div class="stat-value" id="stat-depth">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Messages Processed</div>
                <div class="stat-value" id="stat-processed">0</div>
            </div>
        </div>

        <div class="queues" id="queues"></div>
        
        <div class="refresh-info">
            Metrics update every 2 seconds • Last update: <span id="last-update">never</span>
        </div>
    </div>

    <script>
        function formatTime(timestamp) {
            const date = new Date(timestamp * 1000);
            return date.toLocaleTimeString();
        }

        function truncateContent(content, maxLen = 60) {
            if (content.length > maxLen) {
                return content.substring(0, maxLen) + "...";
            }
            return content;
        }

        function renderMessage(msg, index, isProcessing) {
            const positionLabel = isProcessing ? "⏳ Processing" : `#${index + 1}`;
            const cardClass = isProcessing ? "processing" : "queued";
            
            let avatarHtml = "";
            if (msg.author_avatar_url) {
                avatarHtml = `<img src="${msg.author_avatar_url}" alt="${msg.author}" onerror="this.parentElement.textContent='${msg.author[0]}'">`;
            } else {
                avatarHtml = msg.author[0].toUpperCase();
            }

            let processingInfo = "";
            if (isProcessing && msg.processing_time_seconds !== null) {
                processingInfo = `<div class="processing-indicator">⏱ ${Math.round(msg.processing_time_seconds)}s elapsed</div>`;
            }

            const timeStr = formatTime(msg.created_at);
            const msgId = msg.message_id.substring(0, 8) + "...";
            
            return `
                <div class="message-card ${cardClass}">
                    <div class="message-position">${positionLabel}</div>
                    <div class="message-header">
                        <div class="message-avatar">${avatarHtml}</div>
                        <div class="message-info">
                            <div class="message-author">${msg.author}</div>
                            <div class="message-meta">
                                <strong>#${msg.channel}</strong> • ${timeStr} • ID: ${msgId}
                            </div>
                        </div>
                    </div>
                    <div class="message-content">${truncateContent(msg.content)}</div>
                    ${processingInfo}
                </div>
            `;
        }

        async function updateMetrics() {
            try {
                const response = await fetch('/api/metrics');
                const data = await response.json();
                
                document.getElementById('stat-contexts').textContent = data.total_contexts;
                document.getElementById('stat-workers').textContent = data.active_workers;
                document.getElementById('stat-depth').textContent = data.total_queue_depth;
                document.getElementById('stat-processed').textContent = data.total_messages_processed;
                
                const queuesDiv = document.getElementById('queues');
                queuesDiv.innerHTML = '';
                
                for (const [key, metrics] of Object.entries(data.contexts)) {
                    const card = document.createElement('div');
                    card.className = 'queue-card' + (metrics.active_worker ? ' active' : '');
                    
                    const status = metrics.active_worker ? 'active' : 'idle';
                    const statusColor = metrics.active_worker ? 'active' : 'idle';
                    
                    let messagesHtml = '';
                    
                    // Show currently processing message
                    if (metrics.currently_processing) {
                        messagesHtml += renderMessage(metrics.currently_processing, 0, true);
                    }
                    
                    // Show queued messages
                    if (metrics.queued_messages && metrics.queued_messages.length > 0) {
                        metrics.queued_messages.forEach((msg, idx) => {
                            messagesHtml += renderMessage(msg, idx, false);
                        });
                    }

                    const messagesSection = messagesHtml 
                        ? `<div class="messages-section">
                            <div class="messages-title">Messages</div>
                            ${messagesHtml}
                           </div>`
                        : '';
                    
                    card.innerHTML = `
                        <div class="queue-header">
                            <div class="queue-id">${key}</div>
                            <div class="queue-status">
                                <span class="badge ${statusColor}">${status}</span>
                            </div>
                        </div>
                        <div class="metric">
                            <span class="metric-label">Queue Depth</span>
                            <span class="metric-value">${metrics.queue_size}</span>
                        </div>
                        <div class="metric">
                            <span class="metric-label">Total Processed</span>
                            <span class="metric-value">${metrics.total_processed}</span>
                        </div>
                        ${messagesSection}
                    `;
                    
                    queuesDiv.appendChild(card);
                }
                
                const now = new Date();
                document.getElementById('last-update').textContent = now.toLocaleTimeString();
            } catch (error) {
                console.error('Failed to fetch metrics:', error);
            }
        }
        
        updateMetrics();
        setInterval(updateMetrics, 2000);
    </script>
</body>
</html>
"""


def create_observability_app(metrics_collector: MetricsCollector) -> web.Application:
    """Create an aiohttp web app for metrics observability."""

    app = web.Application()

    async def metrics_api(request: web.Request) -> web.Response:
        """JSON API endpoint for metrics."""
        data = metrics_collector.get_summary()
        return web.json_response(data)

    async def dashboard(request: web.Request) -> web.Response:
        """HTML dashboard."""
        return web.Response(text=HTML_DASHBOARD, content_type="text/html")

    app.router.add_get("/", dashboard)
    app.router.add_get("/api/metrics", metrics_api)

    return app


async def run_observability_server(
    metrics_collector: MetricsCollector, host: str = "127.0.0.1", port: int = 8765
) -> None:
    """Run the observability server. Blocks until stopped."""
    app = create_observability_app(metrics_collector)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("Observability server running at http://%s:%d", host, port)
