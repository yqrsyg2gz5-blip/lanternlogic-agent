import { useEffect, useState } from 'react';
import {
  Package, Film, Image as ImageIcon, Globe, FileCode2, FileText, Download, ExternalLink,
} from 'lucide-react';
import { api } from '../api';
import { Markdown } from './Markdown';
import { API_BASE, authedUrl } from '../api';

/**
 * 产物展示区（Studio 雏形）。
 *
 * **为什么做**：第 24 班截图看界面时发现 —— Agent 干完活（网页/表格/报告/图片）之后，
 * 用户**只能看到侧栏的文件列表**，没有"成果"的展示位。这是对照同类产品
 * 这一代最明显的体验缺口。
 *
 * **安全**：一律走 `/files/raw`（第 17 班 P2-7 已修：可渲染类型带
 * `Content-Security-Policy: sandbox` + `nosniff`，脚本类强制下载）。
 * 所以即便 Agent 生成了带 `<script>` 的 HTML，iframe 里也执行不了、也拿不到同源 API 权限。
 */
type Entry = { name: string; is_dir: boolean; size: number };

const IMG_RE = /\.(png|jpe?g|gif|webp|bmp|ico|svg)$/i;
const HTML_RE = /\.(html?)$/i;
const TEXT_RE = /\.(md|markdown|txt|log|json|csv|tsv|ya?ml)$/i;
// 代码类产物：纯文本预览（等宽 <pre>），不走 Markdown 渲染（第 41 班补）
const CODE_RE = /\.(py|js|mjs|cjs|ts|tsx|jsx|css|scss|sh|bash|bat|ps1|java|go|rs|c|cpp|h|sql|toml|ini|env)$/i;
// 视频产物：内联播放 + 下载（第 41 班：生成完能直接看，不用去文件夹里翻）
const VIDEO_RE = /\.(mp4|webm|m4v|mov)$/i;

function rawUrl(taskId: string, path: string): string {
  // ★ 同上：<img>/<video>/iframe 带不了请求头 ⇒ 走带 token 的地址
  return authedUrl(`${API_BASE}/tasks/${taskId}/files/raw?path=${encodeURIComponent(path)}`);
}

function human(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

/** 极简 CSV 解析（支持双引号包裹与 "" 转义）—— 够用即可，不引依赖 */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++; } else { quoted = false; }
      } else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(cell); cell = ''; }
    else if (c === '\n') { row.push(cell); rows.push(row); row = []; cell = ''; }
    else if (c !== '\r') cell += c;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows.filter((r) => r.some((x) => x.trim() !== ''));
}

export function ArtifactPanel({ taskId, embedded, ping = 0, onCount, initialOpen }: { taskId: string; embedded?: boolean; ping?: number; onCount?: (n: number) => void; initialOpen?: string }) {
  const [files, setFiles] = useState<Entry[]>([]);
  const [openName, setOpenName] = useState<string | null>(null);
  const [cache, setCache] = useState<Record<string, string>>({});
  const [collapsed, setCollapsed] = useState(false);
  const [filter, setFilter] = useState('');

  /** ★ 2026-10-06（用户提的"点了产物就在群里直接看" ✓）：
   *  从群聊点某个产物进来时，**直接展开那一个** ✓ ——
   *  不然还得在列表里再找一遍、再点一次（多一步就多一次困惑 ✓）。 */
  useEffect(() => {
    if (!initialOpen) return;
    setOpenName(initialOpen);
    if ((TEXT_RE.test(initialOpen) || CODE_RE.test(initialOpen)) && cache[initialOpen] === undefined) {
      void fetch(rawUrl(taskId, initialOpen))
        .then((r) => r.text())
        .then((t) => setCache((m) => ({ ...m, [initialOpen]: t.slice(0, 300000) })))
        .catch(() => undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialOpen, taskId]);

  const applyFiles = (list: Entry[]): void => {
    const visible = list.filter((e) => !e.is_dir && !e.name.startsWith('tts_'));
    setFiles(visible);
    onCount?.(visible.length);
  };

  useEffect(() => {
    let alive = true;
    const load = (): void => {
      void api
        .getFiles(taskId)
        .then((list) => {
          if (alive) applyFiles(list);
        })
        .catch(() => {
          /* 任务工作区可能还没建好 */
        });
    };
    load();
    const timer = setInterval(load, 6000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  // 交付即时刷新：TaskView 在 task_done 时 ping+1，不等 6s 轮询
  useEffect(() => {
    if (ping === 0) return; // 初次挂载不触发
    void api
      .getFiles(taskId)
      .then((list) => applyFiles(list))
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ping, taskId]);

  const open = async (name: string): Promise<void> => {
    setOpenName((cur) => (cur === name ? null : name));
    if ((TEXT_RE.test(name) || CODE_RE.test(name)) && cache[name] === undefined) {
      try {
        const r = await fetch(rawUrl(taskId, name));
        const t = await r.text();
        setCache((m) => ({ ...m, [name]: t.slice(0, 300000) }));
      } catch {
        setCache((m) => ({ ...m, [name]: '（读取失败）' }));
      }
    }
  };

  if (files.length === 0) return null;

  const q = filter.trim().toLowerCase();
  const visible = q ? files.filter((f) => f.name.toLowerCase().includes(q)) : files;
  const filterBox =
    files.length > 8 ? (
      <input
        className="art-filter mono"
        placeholder="筛选文件名…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
    ) : null;

  const fileList = (onPick?: (name: string) => void) => (
    <>
      {filterBox}
      {visible.length === 0 && <div className="art-note">无匹配文件</div>}
      <div className="art-list">
        {visible.map((f) => (
          <button
            key={f.name}
            className={`art-item ${openName === f.name ? 'art-on' : ''}`}
            onClick={() => (onPick ? onPick(f.name) : void open(f.name))}
            title={f.name}
          >
            <span className="art-name">
              {(() => {
                  const Ico = VIDEO_RE.test(f.name) ? Film : IMG_RE.test(f.name) ? ImageIcon : HTML_RE.test(f.name) ? Globe : CODE_RE.test(f.name) ? FileCode2 : FileText;
                  return <Ico size={13} style={{ display: 'inline', verticalAlign: -2, marginRight: 4, opacity: 0.8 }} />;
                })()}{f.name}
            </span>
            <span className="ev-meta">{human(f.size)}</span>
          </button>
        ))}
      </div>
    </>
  );

  const preview = (name: string) => {
    const url = rawUrl(taskId, name);
    if (VIDEO_RE.test(name)) return <video className="art-video" src={url} controls preload="metadata" />;
    if (IMG_RE.test(name)) return <img className="art-img" data-preview src={url} alt={name} />;
    if (HTML_RE.test(name)) return <iframe className="art-frame" src={url} title={name} sandbox="" />;
    const body = cache[name];
    if (body === undefined) return <div className="art-loading">读取中…</div>;
    if (/\.(csv|tsv)$/i.test(name)) {
      const rows = parseCsv(body).slice(0, 200);
      return (
        <div className="art-tablewrap">
          <table className="md">
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {r.map((c, j) =>
                    i === 0 ? <th key={j}>{c}</th> : <td key={j}>{c}</td>,
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length >= 200 && <div className="art-note">仅显示前 200 行</div>}
        </div>
      );
    }
    if (CODE_RE.test(name)) {
      return <pre className="art-code mono">{body}</pre>;
    }
    return <Markdown text={body} resolveSrc={(x) => (/^([a-z]+:)?\/\//i.test(x) || x.startsWith("data:") ? x : rawUrl(taskId, x))} />;
  };

  if (files.length === 0) return <div className="aside-empty">还没有产物——任务交付后在这里查看文件</div>;

  if (embedded) {
    return (
      <div className="art-embedded">
        {fileList()}
        {openName && (
          <div className="art-view">
            <div className="art-view-head">
              <span className="mono">{openName}</span>
              <a className="art-open" href={rawUrl(taskId, openName)} download={openName}>
                <Download size={11} style={{ display: "inline", verticalAlign: -1, marginRight: 2 }} />下载
              </a>
              <a className="art-open" href={rawUrl(taskId, openName)} target="_blank" rel="noreferrer">
                新窗口 <ExternalLink size={11} style={{ display: "inline", verticalAlign: -1 }} />
              </a>
            </div>
            <div className="art-view-body">{preview(openName)}</div>
          </div>
        )}
      </div>
    );
  }

  return (
    <aside className={`artpanel ${collapsed ? 'art-collapsed' : ''}`}>
      <div className="art-head">
        <button className="art-toggle" onClick={() => setCollapsed((c) => !c)}>
          {collapsed ? <Package size={13} /> : <><Package size={13} style={{ display: "inline", verticalAlign: -2, marginRight: 4 }} />产物</>}
        </button>
        {!collapsed && <span className="ev-meta">{files.length} 个文件</span>}
      </div>
      {!collapsed && (
        <div className="art-body">
          {fileList()}
          {openName && (
            <div className="art-view">
              <div className="art-view-head">
                <span className="mono">{openName}</span>
                <a className="art-open" href={rawUrl(taskId, openName)} target="_blank" rel="noreferrer">
                  新窗口 <ExternalLink size={11} style={{ display: "inline", verticalAlign: -1 }} />
                </a>
              </div>
              <div className="art-view-body">{preview(openName)}</div>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
