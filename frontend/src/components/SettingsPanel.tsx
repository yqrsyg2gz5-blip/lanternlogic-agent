import { Fragment, useRef, useEffect, useState, useCallback, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import {
  Settings2, Brain, Clapperboard, Wrench, Mic, Blocks, FolderOpen, AlarmClock,
  BarChart3, Info, Puzzle, Clock,
} from 'lucide-react';
import { api, authedUrl, type BudgetStatus, type DataClearPreview, type LanStatus } from '../api';
import { safeHref } from '../lib/safeUrl';
// A5：预设表只有一份（见 src/modelPresets.ts 顶部说明）——设置页不再自建一份
import { MODEL_PRESETS } from '../modelPresets';
import { SectionHead } from './SectionHead';
import { RiskNote } from './RiskNote';
// ★ 第 8b 批：手机直连二维码（编码用 qrcode；解码验证见 scripts/verify_lan_qr.mjs）
import QRCode from 'qrcode';
import type { Automation, Project, Skill } from '../types';

type Section = 'general' | 'model' | 'video' | 'executor' | 'voice' | 'capabilities' | 'skills' | 'projects' | 'automations' | 'stats' | 'about';

const NAV_ICONS: Record<string, any> = {
  general: Settings2, model: Brain, video: Clapperboard, executor: Wrench,
  voice: Mic,
  skills: Blocks, projects: FolderOpen, automations: AlarmClock,
  stats: BarChart3, about: Info,
};
/** 每节的标题与一句说明（"这节是干什么的"）——设置页正规化的一半价值就在这句话上。 */
const SECTION_META: Record<string, { title: string; desc: string }> = {
  general: { title: '常规', desc: '界面语言/主题，以及手机直连（局域网访问）开关与访问密码。' },
  model: { title: '模型设置', desc: '决定 Agent 用哪个"大脑"。没配 Key 时只能用 Mock 演示模式，发消息不会真的回答。' },
  // ★ 2026-10-06（用户提的）：这一节改叫「出图 / 出视频」—— 两块放在一起看 ✓
  video: { title: '出图 / 出视频', desc: '出图（云端通义万相等）与出视频（MiniMax/万相/Seedance/可灵；不启用则用本地 ComfyUI）。' },
  executor: { title: '执行环境', desc: 'Agent 能在哪些目录干活、哪些命令必须先问你、沙箱开不开、超时多久。' },
  voice: { title: '语音', desc: '听（语音转文字，两档：云端 MiMo / 本地离线）与说（语音合成后端）。' },
  // ★ 2026-10-06：能力总览（表格）—— 一眼看清 5 个能力现在用谁、能不能用、备选是谁
  capabilities: { title: '能力总览', desc: '一张表看清「对话 / 出图 / 出视频 / 听 / 说」现在用谁、能不能用、备选是谁。' },
  skills: { title: '技能', desc: 'Agent 可以加载的技能包；每个技能是一个带 SKILL.md 的目录。' },
  projects: { title: '项目', desc: '把任务归类到项目里，便于按项目查看与统计。' },
  automations: { title: '自动化', desc: '定时/触发式地自动跑任务（例如每天早上整理一次下载目录）。' },
  stats: { title: '使用统计', desc: 'token 用量与花费概览，按模型/任务拆分。' },
  about: { title: '关于', desc: '版本、授权状态与项目信息。' },
};

/** 可恢复默认的节（与后端 `_RESETTABLE` 一致；server/storage/skills/mcp 会被后端拒）。
 *  ★ 2026-10-06：加 `tts`（语音合成后端变成配置项了 ✓ 就该能恢复默认 ✓）——
 *  两边不一致有测试盯着（`test_settings_reset.py` ✓ 它当场把我逮住过 ✓）。 */
const RESETTABLE = ['model', 'executor', 'video', 'image', 'asr', 'tts', 'kb', 'ui', 'notify', 'memory'];

const NAV: { group: string; items: [Section, string][] }[] = [  {
    group: '基础设置',
    items: [
      ['general', '常规'],
      ['model', '模型设置'],
      ['video', '出图 / 出视频'],
      ['executor', '执行环境'],
    ['voice', '语音'],
    ],
  },
  {
    group: 'Agent 能力',
    items: [
      // ★ 2026-10-06（用户提的）：能力页做成**表格** —— 一眼看清"每个能力现在用谁、能不能用、退到哪"
      ['capabilities', '能力总览'],
      ['skills', '技能'],
      ['projects', '项目'],
      ['automations', '自动化'],
    ],
  },
  {
    group: '数据与统计',
    items: [
      ['stats', '使用统计'],
      ['about', '关于'],
    ],
  },
];

/** 设置页——全屏双栏（左分类导航 + 右内容卡片），对标 ZCode 设置 */
export function SettingsPanel({ onClose, rootId }: { onClose(): void; rootId?: string }) {
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const [section, setSection] = useState<Section>('model');
  // webhook 管理：secret 只在创建/轮换响应中出现一次，前端暂存以便复制 URL
  // ★ 2026-10-09（AGPL 批）：多带几个字段 —— enforced=false 时界面走「开源版·无使用限制」分支 ✓
  const [lic, setLic] = useState<{ trial_days_left: number; trial_expired: boolean; activated: boolean; kind: string; name: string; exp: string;
                                     enforced?: boolean; mode?: string; note?: string;
                                     source_url?: string; license?: string } | null>(null);
  const [licKey, setLicKey] = useState('');
  const [licMsg, setLicMsg] = useState('');
  const [hookSecrets, setHookSecrets] = useState<Record<string, string>>({});
  // ★ 2026-10-06「这类以后都别问」：程序名 → 记住时间（在「执行环境」那节展示与收回 ✓）
  const [foreverRules, setForeverRules] = useState<Record<string, string>>({});
  const [foreverBusy, setForeverBusy] = useState(false);
  const [hookCopied, setHookCopied] = useState<Record<string, boolean>>({});
  const [settings, setSettings] = useState<Record<string, any> | null>(null);
  const [provider, setProvider] = useState('');
  const [modelName, setModelName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [keyEnv, setKeyEnv] = useState('');
  // ★ Phase 1 ①：默认勾上（写 User 级环境变量）——否则填的 Key 只活在当前进程里，
  //   后端一重启就"发消息不回复"（2026-10-04 血案的根因）。
  const [persistKey, setPersistKey] = useState(true);
  // ★ 2026-10-06：步数上限 + 单价表（此前只能改配置文件）
  const [maxIter, setMaxIter] = useState(25);
  const [priceRows, setPriceRows] = useState<{ model: string; inp: string; out: string }[]>([]);
  const [limitsBusy, setLimitsBusy] = useState(false);
  const [limitsMsg, setLimitsMsg] = useState('');
  const [limitsOk, setLimitsOk] = useState(false);
  // ★ 2026-10-07「每个 Key 花费上限」（用户点名要的：只有成本显示 ✗ 没有上限 ✗）
  const [budget, setBudget] = useState<BudgetStatus | null>(null);
  const [budgetDraft, setBudgetDraft] = useState<Record<string, string>>({});
  const [budgetMsg, setBudgetMsg] = useState('');
  const [budgetBusy, setBudgetBusy] = useState(false);
  // ★ Phase 2 ⑥：在线模型列表（source=live 才有意义；preset = 拉不到，退回内置预设）
  const [modelOpts, setModelOpts] = useState<string[]>([]);
  const [modelNote, setModelNote] = useState('');
  const [modelSource, setModelSource] = useState<'live' | 'preset' | ''>('');
  const [modelsBusy, setModelsBusy] = useState(false);

  const loadModels = useCallback((refresh = false) => {
    // 预设置里的 `models` 是给人看的一句话（"v2.6-flash / v2.5 / …"），不是数组 ——
    // 所以拉不到在线列表时，退回"预设默认模型名 + 用户当前填的"这两项，别去猜解析。
    const presetName = (MODEL_PRESETS as Record<string, { model_name?: string }>)[provider]?.model_name;
    const fallback = [presetName, modelName].filter((x): x is string => !!x && x.trim() !== '');
    setModelsBusy(true);
    void api
      .listModels(provider || undefined, baseUrl || undefined, refresh)
      .then((r) => {
        const live = r.source === 'live' && r.models.length > 0;
        setModelOpts(live ? r.models : Array.from(new Set(fallback)));
        setModelSource(r.source);
        setModelNote(live
          ? `${r.note}${r.cached ? '（缓存）' : ''}`
          : `${r.note}；仍可直接手填，或选下面的预设名`);
      })
      .catch((e) => {
        setModelOpts(Array.from(new Set(fallback)));
        setModelSource('preset');
        setModelNote(`拉取失败（${e}）—— 用内置预设`);
      })
      .finally(() => setModelsBusy(false));
    // modelName 故意不进依赖：它每次输入都会变，重拉列表会把下拉框抖掉
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, baseUrl]);

  // 进入「模型设置」或换提供者时自动拉一次（用户不必先按刷新才看得到列表）
  useEffect(() => {
    if (section !== 'model') return;
    loadModels();
  }, [section, provider, loadModels]);

  // ★ Phase 2 ④⑤：「语音」栏用能力槽接口 —— 每个能力现在用谁/能不能用/退到哪
  const [caps, setCaps] = useState<Awaited<ReturnType<typeof api.getCapabilities>> | null>(null);
  const [asrBusy, setAsrBusy] = useState(false);
  const [asrNote, setAsrNote] = useState('');
  // ★ 2026-10-06：语音合成后端切换（用户实测问："切换能做到吗？" ⇒ 以前不能 ✗ 现在能 ✓）
  const [ttsNote, setTtsNote] = useState('');
  const [ttsBusy, setTtsBusy] = useState(false);
  // ★ 2026-10-08：当前选的音色（千问3 的 9 个之一 ✓ 别的后端忽略它 ✓ 见 config.TtsCfg.voice ✓）
  const [ttsVoice, setTtsVoice] = useState('');
  const loadCaps = useCallback(() => {
    void api.getCapabilities().then(setCaps).catch(() => undefined);
  }, []);
  const switchTts = useCallback((backend: string) => {
    setTtsBusy(true);
    setTtsNote('');
    void api.setTts(backend)
      .then((r) => { setTtsNote(r.note); loadCaps(); })
      .catch((e) => setTtsNote(e instanceof Error ? e.message : String(e)))
      .finally(() => setTtsBusy(false));
  }, [loadCaps]);

  // ★ 2026-10-07「一键装 + 一键下模型」（用户：*"我想要的就是一键能安装，然后还能使用这种的"*）
  //   装/下都在后端跑 ✓ 这里**轮询**它的状态 ✓ 把**真实输出**贴出来 ✓（不装作成功 ✓）
  const [asrJob, setAsrJob] = useState<{ kind: string; state: string; lines: string[]; bytes: number; elapsed: number }>(
    { kind: '', state: 'idle', lines: [], bytes: 0, elapsed: 0 });
  const [asrTier, setAsrTier] = useState('0.6b');
  const asrRunning = asrJob.state === 'running';
  useEffect(() => {
    let alive = true;
    const tick = (): void => {
      void api.localAsrInstallStatus()
        .then((s) => { if (alive) setAsrJob({ kind: s.kind ?? '', state: s.state, lines: s.lines ?? [], bytes: s.bytes ?? 0, elapsed: s.elapsed ?? 0 }); })
        .catch(() => undefined);
    };
    tick();
    const timer = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  const startInstall = useCallback(() => {
    void api.installLocalAsr().catch((e) => setAsrNote(e instanceof Error ? e.message : String(e)));
  }, []);
  const startPull = useCallback((tier: string) => {
    void api.pullLocalAsrModel(tier).catch((e) => setAsrNote(e instanceof Error ? e.message : String(e)));
  }, []);
  /** ★ 2026-10-08：**本地朗读「一键装」**（用户："就像上面这个千问3似的，点一下它就安装"）
   *  —— 与 ASR 那套**同一把锁、同一个状态接口** ✓（两条 pip 撞一起会互相毁掉 ✗）*/
  const startTtsInstall = useCallback((engine: string) => {
    setTtsNote('');
    void api.installTtsEngine(engine).catch((e) => setTtsNote(e instanceof Error ? e.message : String(e)));
  }, []);

  // ★ 2026-10-07：知识库向量档位（用户："我做这个知识库本地的" ✓）
  const [kbTierNote, setKbTierNote] = useState('');
  const [kbTierBusy, setKbTierBusy] = useState(false);
  // ★ 2026-10-07：主题切换（用户："最起码两个 —— 深色 + 白色"✓）
  const [theme, setTheme] = useState<string>(
    () => (typeof document !== 'undefined' && document.documentElement.dataset.theme) || 'dark');
  const [themeNote, setThemeNote] = useState('');
  const [themeBusy, setThemeBusy] = useState(false);
  const switchTheme = useCallback((t: 'dark' | 'light') => {
    setThemeBusy(true);
    setThemeNote('');
    document.documentElement.dataset.theme = t;      // ① 立刻变 ✓（不等网络 ✓）
    localStorage.setItem('theme', t);                // ② 记住 ✓
    setTheme(t);
    void api.setUiTheme(t)
      .then(() => setThemeNote(t === 'light' ? '已切到浅色 ✓ 已记住 ✓' : '已切回深色 ✓ 已记住 ✓'))
      .catch((e) => setThemeNote('界面已切换 ✓ 但没能存进配置：' + String(e)))
      .finally(() => setThemeBusy(false));
  }, []);

  const switchKb = useCallback((embedder: string) => {
    setKbTierNote('');
    void api.setKbEmbedder(embedder)
      .then((r) => { setKbTierNote(r.note); loadCaps(); })
      .catch((e) => setKbTierNote(e instanceof Error ? e.message : String(e)))
      .finally(() => setKbTierBusy(false));
  }, [loadCaps]);
  useEffect(() => {
    // ★ 2026-10-07（**截图当场抓到的真 bug** ✗）：知识库那张卡在「常规」节里 ✓
    //   而这里原来只给「语音 / 能力总览」拉能力数据 ✗
    //   ⇒ 知识库的向量档位**永远显示"正在读取…"** ✗✓（界面看着像坏了 ✓）。
    //   截图救了一次 ✓ —— 所以每次改界面都必须真看一眼 ✓ 不能只看测试绿 ✓。
    if (section === 'voice' || section === 'capabilities' || section === 'general') loadCaps();
  }, [section, loadCaps]);
  useEffect(() => {
    // ★ 再加一道：**挂载时就拉一次** ✓ ——
    //   实测：只靠"切节触发"时，首次那个请求可能赶在 token 就绪之前发出而静默失败 ✗
    //   （`loadCaps` 里是 `.catch(() => undefined)` ✓ 不报错 ✓ 于是界面就一直"正在读取…"✗✓）
    //   ⇒ 挂载时补一次 ✓ 用户一进来就能看到真实档位 ✓。
    loadCaps();
  }, [loadCaps]);

  // ★ 2026-10-06「这类以后都别问」：进「执行环境」就拉一次清单（用户随时能看到自己放过什么 ✓）
  const loadForever = useCallback(() => {
    void api.listForeverApprovals().then((r) => setForeverRules(r.rules ?? {})).catch(() => undefined);
  }, []);
  const revokeForever = useCallback((key: string) => {
    setForeverBusy(true);
    void api.revokeForeverApproval(key)
      .then((r) => setForeverRules(r.rules ?? {}))
      .catch(() => undefined)
      .finally(() => setForeverBusy(false));
  }, []);
  useEffect(() => {
    if (section === 'executor') loadForever();
  }, [section, loadForever]);

  // ★ A-4：查看密码（仅本机）/ 改密码
  const [tokenShown, setTokenShown] = useState<string | null>(null);
  const [tokenNote, setTokenNote] = useState('');
  const [tokenBusy, setTokenBusy] = useState(false);
  const [pwOpen, setPwOpen] = useState(false);
  const [pwDraft, setPwDraft] = useState('');
  const [pwErr, setPwErr] = useState('');
  const [pwBusy, setPwBusy] = useState(false);
  // ★ 设置导出/导入：portPending = 预演通过、等用户确认的那份
  const [portNote, setPortNote] = useState('');
  const [portPending, setPortPending] = useState<{ sections: Record<string, unknown>; summary: string[] } | null>(null);

  /** 与后端同款：32 字符 url-safe 随机串（≈192 位熵）。 */
  const randomToken = (): string => {
    const a = new Uint8Array(24);
    crypto.getRandomValues(a);
    return btoa(String.fromCharCode(...a)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  };

  const loadToken = (): void => {
    setTokenBusy(true);
    setTokenNote('');
    void api.getAccessToken()
      .then((r) => { setTokenShown(r.token); setTokenNote(r.note); })
      .catch((e) => { setTokenShown(null); setTokenNote(e instanceof Error ? e.message : String(e)); })
      .finally(() => setTokenBusy(false));
  };

  // ★ 高风险项说明：把"当前值处于什么状态"算出来（**只读**，不发请求、不改值）
  const dirsRisk = (): { level: 'ok' | 'warn' | 'danger'; summary: string } => {
    const dirs = dirsText.split('\n').map((t) => t.trim()).filter(Boolean);
    const wide = dirs.filter((d) => /^[a-zA-Z]:[\\/]?$/.test(d) || /^[a-zA-Z]:[\\/](windows|users|program)/i.test(d)
      || /^[\\/]$/.test(d) || d === '~' || d.includes('..'));
    if (wide.length) {
      return { level: 'danger', summary: `授权目录里有 ${wide.length} 个"大范围"目录（盘符根/系统或用户目录）—— Agent 对这些位置可读写、可删除` };
    }
    if (dirs.length > 1) {
      return { level: 'warn', summary: `已放开 ${dirs.length} 个目录；范围越大，误删误改的面越大` };
    }
    return { level: 'ok', summary: dirs.length ? '只放开了工作区以外的 1 个目录，范围可控' : '只允许在工作区里干活（默认，最安全）' };
  };

  const approvalRisk = (): { level: 'ok' | 'warn' | 'danger'; summary: string } => {
    const list = approvalText.split(',').map((t) => t.trim().toLowerCase()).filter(Boolean);
    if (!list.length) {
      return { level: 'danger', summary: '审批清单是空的 —— 所有命令（含删除、格式化）都不会再问你，直接执行' };
    }
    const del = ['rm', 'del', 'rmdir', 'rd', 'erase', 'format', 'reg', 'remove-item'];
    const missing = del.filter((d) => !list.includes(d));
    if (missing.length) {
      return { level: 'warn', summary: `审批清单缺了 ${missing.length} 个删除类命令（${missing.slice(0, 3).join(' / ')}…）—— 这些命令不会问你` };
    }
    return { level: 'ok', summary: `删除类命令都会先问你（清单 ${list.length} 项）` };
  };

  const switchAsr = (pid: string): void => {    setAsrBusy(true);
    setAsrNote('');
    void api
      .setAsrTier(pid)
      .then((r) => { setAsrNote(r.note); loadCaps(); })
      .catch((e) => setAsrNote(`切换失败：${e}`))
      .finally(() => setAsrBusy(false));
  };
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState('');

  const [dirsText, setDirsText] = useState('');
  const [approvalText, setApprovalText] = useState('');
  const [timeoutText, setTimeoutText] = useState('60');
  const [sandboxMode, setSandboxMode] = useState('off');
  const [sandboxImg, setSandboxImg] = useState('python:3.12-slim');
  const [exNote, setExNote] = useState('');
  const [exSaving, setExSaving] = useState(false);

  // 视频生成设置（第 41 班）：主引擎 + 多引擎 Key，照模型设置的模式
  const [vProvider, setVProvider] = useState('');
  const [vModel, setVModel] = useState('');
  const [vRes, setVRes] = useState('480P');
  const [vKeyEnv, setVKeyEnv] = useState('');
  const [vApiKey, setVApiKey] = useState('');
  const [vSaving, setVSaving] = useState(false);
  const [vNote, setVNote] = useState('');
  const [engDraft, setEngDraft] = useState<Record<string, { keyEnv: string; apiKey: string }>>({});

  const [projects, setProjects] = useState<Project[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [autos, setAutos] = useState<Automation[]>([]);
  const [npName, setNpName] = useState('');
  const [npPrompt, setNpPrompt] = useState('');
  const [naName, setNaName] = useState('');
  const [naInput, setNaInput] = useState('');
  const [naMinutes, setNaMinutes] = useState(60);
  // ★ 2026-10-06：触发方式三选一（每天 / 每隔 N 分钟 / **文件夹有变化** ✓）
  const [naTrigger, setNaTrigger] = useState('daily');
  // ★ 2026-10-07（第 7 项）：支持"每小时 / 每周 / 每月"后多出来的几个输入 ✓
  const [naWeekday, setNaWeekday] = useState(1);        // ISO：1=周一 ✓
  const [naMonthDay, setNaMonthDay] = useState(1);
  const [naMaxCost, setNaMaxCost] = useState('');       // 单次花费上限（留空 = 不限 ✓）
  const [naRetries, setNaRetries] = useState('');       // 失败自动重试次数（留空 = 不重试 ✓）
  // ★ 2026-10-07（第 9 项）：跨任务审计流水（进"统计"那栏时懒加载 ✓ 不必开页就查 ✓）
  const [audit, setAudit] = useState<Awaited<ReturnType<typeof api.getAudit>> | null>(null);
  const [auditKind, setAuditKind] = useState('');
  const [auditErr, setAuditErr] = useState('');
  const loadAudit = useCallback((kind?: string) => {
    return api.getAudit(200, kind ?? auditKind)
      .then((r) => { setAudit(r); setAuditErr(''); })
      .catch((e) => setAuditErr(e instanceof Error ? e.message : String(e)));
  }, [auditKind]);
  useEffect(() => {
    if (section === 'stats' && !audit) void loadAudit('');
  }, [section, audit, loadAudit]);
  const [naWatchPath, setNaWatchPath] = useState('');
  const [naWatchSec, setNaWatchSec] = useState(60);
  const [naErr, setNaErr] = useState('');
  const [naTime, setNaTime] = useState('09:00');
  const [usage, setUsage] = useState<Record<string, any> | null>(null);
  const [memory, setMemory] = useState<Record<string, any> | null>(null);
  const [kbs, setKbs] = useState<any[]>([]);
  const [kbName, setKbName] = useState('');
  const [kbFolder, setKbFolder] = useState('');
  const [kbBusy, setKbBusy] = useState(false);
  const [searxngText, setSearxngText] = useState('');
  // ★ 第 8 批 问题4：手机直连（局域网）——一键开通，用户不必再手改 config.json
  // ★ 设置页正规化：每节的说明 + 状态 + 恢复默认
  const sectionStatus = (): string => {
    const s = settings as any;
    switch (section) {
      case 'general': {
        // 注意：这里**不读 `lan` 状态**（它在本组件里声明得更靠后，箭头函数会踩 TDZ）。
        // 端口与"有没有设密码"从 settings 里取，够用且没有顺序依赖。
        return `端口 ${s?.server?.port ?? '—'} · ${s?.server?.access_token ? '已设密码' : '无密码'}`;
      }
      case 'model': {
        const k = s?.model?.key_set;
        return `${s?.model?.provider ?? '—'} · ${k === true ? '已配 Key' : k === false ? '未配 Key' : '不需要 Key'}`;
      }
      case 'video':
        return s?.video?.provider ? `引擎：${s.video.provider}` : '未启用（用本地 ComfyUI）';
      case 'executor':
        return `沙箱=${s?.executor?.sandbox ?? '—'} · 超时 ${s?.executor?.timeout_seconds ?? '—'}s`;
      case 'voice': {
        const asr = caps?.capabilities?.asr;
        const cur = asr?.providers?.[asr.current];
        return `听：${cur ? (cur.state === 'ready' ? '可用' : cur.state === 'not_installed' ? '未安装' : '缺 Key') : '—'} · 说：MeloTTS`;
      }
      case 'skills':
        return `${skills.length} 个技能`;
      case 'projects':
        return `${projects.length} 个项目`;
      case 'automations':
        return `${autos.length} 条自动化`;
      default:
        return '';
    }
  };

  const sectionTone = (): 'ok' | 'warn' | 'plain' => {
    const s = settings as any;
    switch (section) {
      case 'model':
        return s?.model?.key_set === false ? 'warn' : 'ok';
      case 'video':
        return s?.video?.provider ? 'ok' : 'warn';
      case 'voice':
        return caps?.capabilities?.asr?.current_state === 'ready' ? 'ok' : 'warn';
      default:
        return 'plain';
    }
  };

  const doReset = (sec: string): void => {
    const label = SECTION_META[sec]?.title ?? sec;
    if (!window.confirm(`把「${label}」恢复成出厂默认？\n\n只影响这一节，其它设置不动。`)) return;
    void api
      .resetSettings(sec)
      .then((r) => { setNote(r.note ?? '已恢复默认'); load(); })
      .catch((e) => setNote(`恢复默认失败：${e}`));
  };

  // ★ 2026-10-07「一键清干净」（用户点名要的 ✓ 破坏性 ⇒ 二次确认 ✓）
  const [clearPv, setClearPv] = useState<DataClearPreview | null>(null);
  const [clearPick, setClearPick] = useState<string[]>([]);
  const [clearWord, setClearWord] = useState('');
  const [clearMsg, setClearMsg] = useState('');
  const [clearBusy, setClearBusy] = useState(false);
  // ★ 2026-10-07：出图引擎的切换（体检⑥ 引出来的缺口：此前界面上切不了 ✗）
  const [imgBusy, setImgBusy] = useState(false);
  const [imgMsg, setImgMsg] = useState('');
  // ★ 2026-10-07（第 3 项）：版本 + 检查更新（关于页 ✓ 版本号从**后端**读 ✓ 不再硬编码 ✗）
  const [ver, setVer] = useState<{ name: string; version: string; vendor: string } | null>(null);
  // ★★ 2026-10-08：作者卡（只读 ✓ 验签结论由后端给 ✓）+ 展开/收起（邮箱微信默认不显示 ✓）
  const [auth, setAuth] = useState<{ org: string; line: string; email: string; wechat: string;
                                     alias: string; signed_at: string; verified: boolean;
                                     reason: string; fingerprint: string } | null>(null);
  const [authOpen, setAuthOpen] = useState(false);
  const [upd, setUpd] = useState<{ has_update: boolean; note: string; notes: string; url: string } | null>(null);
  const [updBusy, setUpdBusy] = useState(false);
  const [updMsg, setUpdMsg] = useState('');

  // ★ 2026-10-10：浏览器实况缓存垃圾（每开一次浏览器留一个 Chromium profile ✗ 实测攒到约 2GB）
  //   —— 实测教训：它还会被快照整套复制、被备份一起打包 ⇒ 备份卡死后端那次就有它一份 ✗
  const [bcStat, setBcStat] = useState<Awaited<ReturnType<typeof api.browserCacheStatus>> | null>(null);
  const [bcBusy, setBcBusy] = useState(false);
  const [bcMsg, setBcMsg] = useState('');
  const loadBrowserCache = useCallback(() => {
    void api.browserCacheStatus().then(setBcStat).catch(() => undefined);
  }, []);
  const doCleanBrowserCache = (): void => {
    setBcBusy(true);
    setBcMsg('');
    void api
      .cleanBrowserCache()
      .then((r) => { setBcMsg('✅ ' + r.reason); loadBrowserCache(); })
      .catch((e) => setBcMsg('清理失败：' + (e instanceof Error ? e.message : String(e))))
      .finally(() => setBcBusy(false));
  };

  const loadClear = (): void => {
    void api.getDataClear()
      .then((d) => { setClearPv(d); setClearPick(d.items.filter((i) => i.exists).map((i) => i.key)); })
      .catch(() => setClearPv(null));
  };

  const doClear = (): void => {
    if (!clearPv) return;
    if (clearPv.running.length) { setClearMsg('还有任务在跑 —— 先等它结束或取消，再清 ✗'); return; }
    if (clearWord.trim() !== clearPv.phrase) { setClearMsg(`请把确认词原样打出来：「${clearPv.phrase}」`); return; }
    if (!clearPick.length) { setClearMsg('没有选中任何要清的东西'); return; }
    setClearBusy(true);
    setClearMsg('');
    void api.clearData(clearWord.trim(), clearPick)
      .then((r) => { setClearMsg(`✅ ${r.note}`); setClearWord(''); load(); loadClear(); })
      .catch((e) => setClearMsg(`清空失败：${e instanceof Error ? e.message : String(e)}`))
      .finally(() => setClearBusy(false));
  };

  // ★ 2026-10-07「每个 Key 花费上限」：存上限 / 清零重来。
  //   规矩（与项目其它设置一致 ✓）：**说清改完会怎样** ✓ 失败原样报出来 ✓ 不许假装成功 ✗
  const saveCap = (key: string, raw: string | undefined, reset = false): void => {
    const txt = (raw ?? '').trim();
    const val = txt === '' ? 0 : Number(txt);       // 空 = 撤掉上限（0 = 不拦 ✓）
    if (!Number.isFinite(val) || val < 0) {
      setBudgetMsg('上限要填一个不小于 0 的数（填 0 = 撤掉这把 Key 的上限）');
      return;
    }
    setBudgetBusy(true);
    setBudgetMsg('');
    void api.setBudget(reset ? { key, reset_since: true } : { key, limit: val })
      .then((st) => {
        setBudget(st);
        setBudgetMsg(st.note || '已保存');
        setBudgetDraft((m) => ({ ...m, [key]: '' }));   // 存完清空输入框（防重复点）
      })
      .catch((e) => setBudgetMsg(`保存失败：${e instanceof Error ? e.message : String(e)}`))
      .finally(() => setBudgetBusy(false));
  };

  // ★ 2026-10-06：保存"步数上限 + 单价表"。空行忽略；只填一项也能保存（后端按字段合并）。
  const saveLimits = (): void => {
    setLimitsBusy(true);
    setLimitsMsg('');
    const pricing: Record<string, { in?: number; out?: number }> = {};
    for (const r of priceRows) {
      const name = r.model.trim();
      if (!name) continue;
      const row: { in?: number; out?: number } = {};
      if (r.inp !== '') row.in = Number(r.inp);
      if (r.out !== '') row.out = Number(r.out);
      if (Object.keys(row).length) pricing[name] = row;
    }
    void api.setLimits({ max_iterations: maxIter, pricing })
      .then((res) => {
        setLimitsOk(true);
        setLimitsMsg(`已保存 · 单任务最大步数 ${res.max_iterations} · ${res.note}`);
      })
      .catch((e) => { setLimitsOk(false); setLimitsMsg(`保存失败：${e}`); })
      .finally(() => setLimitsBusy(false));
  };

  // ★ 2026-10-06：一键查单价。**先说清楚**：服务商自己的 API 不给价 ✗，
    //   这里查的是 OpenRouter 的公开价格表；命中就填进表格（元/百万），查不到就如实说 ✓。
    const autoFetchPrice = (): void => {
      setLimitsBusy(true);
      setLimitsMsg('正在查询…');
      const want = modelName || String(settings?.model && (settings.model as any).model_name || '');
      void api.fetchPricing({ model: want })
        .then((r) => {
          if (r.found && r.pricing) {
            const name = want || r.matched || 'model';
            const row = {
              model: name,
              inp: r.pricing.in != null ? String(r.pricing.in) : '',
              out: r.pricing.out != null ? String(r.pricing.out) : '',
            };
            setPriceRows((rows) => {
              const i = rows.findIndex((x) => x.model === name);
              return i >= 0 ? rows.map((x, j) => (j === i ? row : x)) : [...rows, row];
            });
            setLimitsOk(true);
            setLimitsMsg(r.note + '（点「保存上限与单价」生效）');
          } else {
            setLimitsOk(false);
            setLimitsMsg(r.note);
          }
        })
        .catch((e) => { setLimitsOk(false); setLimitsMsg(`查价失败：${e}`); })
        .finally(() => setLimitsBusy(false));
    };

  const [lan, setLan] = useState<LanStatus | null>(null);
  const [lanBusy, setLanBusy] = useState(false);
  const [lanErr, setLanErr] = useState('');
  const [lanCopied, setLanCopied] = useState(false);
  const [qrDataUrl, setQrDataUrl] = useState('');

  // 生成二维码（只在"已开通"且有链接时）——手机扫一下就进去，不用手打 32 位密码。
  useEffect(() => {
    const url = lan?.enabled ? lan.url : '';
    if (!url) { setQrDataUrl(''); return; }
    let alive = true;
    void QRCode.toDataURL(url, { margin: 1, width: 336, errorCorrectionLevel: 'M' })
      .then((d) => { if (alive) setQrDataUrl(d); })
      .catch(() => { if (alive) setQrDataUrl(''); });
    return () => { alive = false; };
  }, [lan?.enabled, lan?.url]);

  useEffect(() => {
    const ex = settings?.executor as Record<string, unknown> | undefined;
    if (!ex) return;
    setDirsText(((ex.allowed_dirs as string[] | undefined) ?? []).join('\n'));
    setApprovalText(((ex.approval_required as string[] | undefined) ?? []).join(', '));
    setTimeoutText(String(ex.timeout_seconds ?? 60));
    setSearxngText(String(ex.searxng_url ?? ''));
    setSandboxMode(String(ex.sandbox ?? 'off'));
    setSandboxImg(String(ex.sandbox_image ?? 'python:3.12-slim'));
  }, [settings]);

  const load = () => {
    void api.lanStatus().then(setLan).catch(() => undefined);   // ★ 问题4：手机直连状态
    // ★ 2026-10-07：每个 Key 的花费上限（用户点名要的）—— 与设置一起拉 ✓
    void api.getBudget().then(setBudget).catch(() => undefined);
    loadClear();   // ★ 2026-10-07：一键清干净的清单（会清什么/不碰什么 ✓）
    loadBrowserCache();   // ★ 2026-10-10：浏览器缓存垃圾体检（只读 ✓）
    void api.getSettings().then((s) => {
      setSettings(s);
      const m = (s as any).model ?? {};
      setProvider(m.provider ?? 'mock');
      setModelName(m.model_name ?? '');
      setBaseUrl(m.base_url ?? '');
      setKeyEnv(m.api_key_env ?? '');
      // ★ 2026-10-06：把已有的上限与单价读进表单（没有就保持默认/空）
      if (typeof m.max_iterations === 'number') setMaxIter(m.max_iterations);
      const pr = (s as any).pricing ?? {};
      setPriceRows(Object.entries(pr).map(([model, v]: [string, any]) => ({
        model,
        inp: v?.in != null ? String(v.in) : '',
        out: v?.out != null ? String(v.out) : '',
      })));
      const v = (s as any).video ?? {};
      setVProvider(v.provider ?? '');
      setVModel(v.model ?? '');
      setVRes(v.resolution || '480P');
      setVKeyEnv(v.api_key_env ?? '');
    });
  };
  useEffect(load, []);

  useEffect(() => {
    if (section === 'skills') void api.listSkills().then(setSkills);
    if (section === 'projects') void api.listProjects().then(setProjects);
    if (section === 'automations') void api.listAutomations().then(setAutos);
    if (section === 'stats') void api.getUsage().then(setUsage);
    if (section === 'about') void api.getLicense().then(setLic).catch(() => undefined);
    // ★ 2026-10-07（第 3 项）：版本号从**后端**读 ✓（关于页不再硬编码 ✗ 一处声明处处读它 ✓）
    if (section === 'about') void api.getVersion().then(setVer).catch(() => undefined);
    // ★★ 2026-10-08：作者卡（**验签结论也来自后端** ✓ 前端不自己判 ✓ 更不写死 ✗）
    if (section === 'about') void api.getAuthorCard().then(setAuth).catch(() => undefined);
  }, [section]);

  const saveModel = () => {
    setSaving(true);
    setNote('');
    void api
      .setModel(provider, modelName, baseUrl || null, apiKey || null, keyEnv ?? null, persistKey,
                // ★ 2026-10-10：填了新 Key 就**先验一次**（服务商拒收 ⇒ 后端不保存并说清原因）
                //   ★ keyEnv 用 `?? null`（不是 `|| null`）—— 本地服务要的就是**空串** ✓
                //     传空串 ⇒ 后端把 api_key_env 清掉 ✓（否则会残留上一家的变量名 ✗）
                Boolean(apiKey))
      .then((r) => {
        setNote(r.note ?? '已保存');
        setApiKey('');
        load();
      })
      .catch((e) => setNote(`保存失败：${e}`))
      .finally(() => setSaving(false));
  };

  const saveExecutor = async (): Promise<void> => {
    setExSaving(true);
    setExNote('');
    try {
      const r = await api.setExecutor({
        allowed_dirs: dirsText.split('\n').map((t) => t.trim()).filter(Boolean),
        approval_required: approvalText.split(',').map((t) => t.trim()).filter(Boolean),
        timeout_seconds: Number(timeoutText),
        searxng_url: searxngText.trim(),
        sandbox: sandboxMode,
        sandbox_image: sandboxImg.trim(),
      });
      setExNote(r.note ?? '已保存');
      setSettings(await api.getSettings());
    } catch (e) {
      setExNote('保存失败：' + (e instanceof Error ? e.message : String(e)));
    } finally {
      setExSaving(false);
    }
  };

  // ★ A5（审计台账 P1）：这份表以前**写在这里**，与切换器那份（modelPresets.ts）
  //   各维护一份、互不一致（8 vs 6）⇒ 设置页能配 Claude，切换器里却没有它，
  //   切走就切不回来、按钮只显示原始模型 id。现在只有一份：src/modelPresets.ts。
  //   交叉一致性由 tests/test_model_presets_single_source.py 钉住（含"每个
  //   provider 必须存在于后端 _REGISTRY"）。下划线别名只为少改下面几处调用点。
  const PRESETS = MODEL_PRESETS;

  const pickPreset = (name: string) => {
    setProvider(name);
    const p = PRESETS[name];
    if (p) {
      setModelName(p.model_name);
      setBaseUrl(p.base_url);
      setKeyEnv(p.key_env);
    }
    // ★ 2026-10-10（用户实测："换服务商还得再点一次保存，不对劲；DSH 是选一下就成"）：
    //   换服务商**立刻落盘** ✓ —— 不再要求用户额外点一次「保存模型配置」✗
    //   （与首页/任务页那个模型切换器同款行为 ✓ 两处一致 ✓）
    //   ★ 只提交 provider / 模型名 / 地址 / 变量名 ✗ **不带 Key** ✓
    //     ⇒ 各家的 Key 一个字都不动 ✓（切换不需要重填、也不需要重新保存 ✓）
    setApiKey('');
    if (p) {
      void api
        .setModel(name, p.model_name, p.base_url || null, null, p.key_env || null)
        .then(() => {
          setNote(`已切到「${name}」并保存 ✓ 模型列表会自动重拉一次`);
          load();
        })
        .catch((e) => setNote(`切换没保存上：${e}`));
    }
  };

  const VIDEO_PRESETS: Record<string, { model: string; resolution: string; key_env: string; label: string }> = {
    wan: { model: 'wan3.0-video', resolution: '480P', key_env: 'DASHSCOPE_API_KEY', label: '万相 3.0（阿里）' },
    minimax: { model: 'MiniMax-H3', resolution: '480P', key_env: 'MINIMAX_API_KEY', label: '海螺 H3（MiniMax）' },
    seedance: { model: 'doubao-seedance-2-5', resolution: '480P', key_env: 'ARK_API_KEY', label: 'Seedance（字节）' },
    kling: { model: 'kling-v3', resolution: '480P', key_env: 'KLING_API_KEY', label: '可灵（快手）' },
  };

  // 各引擎的档位与价格（只填官方精确价，拿不到准数的不标价——用户明确要求）。
  // wan：阿里云百炼国内站标准价 0.3/0.6/1.2 元/秒；minimax 官方仅公布 2K=0.8 元/秒，
  // 480P/768P 无官方精确单价 → 不标价；seedance/kling 未见官方精确单价 → 不标价。
  const VIDEO_RESOLUTIONS: Record<string, { value: string; label: string }[]> = {
    wan: [
      { value: '480P', label: '480P · 0.30 元/秒（5 秒约 1.50 元）' },
      { value: '720P', label: '720P · 0.60 元/秒（5 秒约 3.00 元）' },
      { value: '1080P', label: '1080P · 1.20 元/秒（5 秒约 6.00 元）' },
    ],
    minimax: [
      { value: '480P', label: '480P' },
      { value: '768P', label: '768P' },
    ],
    seedance: [
      { value: '480P', label: '480P' },
      { value: '720P', label: '720P' },
      { value: '1080P', label: '1080P' },
    ],
    kling: [
      { value: '480P', label: '480P' },
      { value: '720P', label: '720P' },
    ],
  };
  const resOptions = VIDEO_RESOLUTIONS[vProvider] ?? [
    { value: '480P', label: '480P' },
    { value: '720P', label: '720P' },
    { value: '1080P', label: '1080P' },
  ];

  const pickVideoPreset = (name: string) => {
    setVProvider(name);
    const p = VIDEO_PRESETS[name];
    if (p) {
      setVModel(p.model);
      setVRes(p.resolution);
      setVKeyEnv(p.key_env);
    }
  };

  const saveVideo = (): void => {
    setVSaving(true);
    setVNote('');
    const engines: Record<string, { api_key_env: string; api_key?: string }> = {};
    for (const [name, d] of Object.entries(engDraft)) {
      if (d.keyEnv.trim()) {
        engines[name] = { api_key_env: d.keyEnv.trim() };
        if (d.apiKey.trim()) engines[name].api_key = d.apiKey.trim();
      }
    }
    void api
      .setVideo({
        provider: vProvider,
        model: vModel,
        resolution: vRes,
        api_key_env: vKeyEnv,
        ...(vApiKey ? { api_key: vApiKey } : {}),
        ...(Object.keys(engines).length ? { engines } : {}),
      })
      .then((r) => {
        setVNote(r.note ?? '已保存');
        setVApiKey('');
        setEngDraft({});
        load();
      })
      .catch((e) => setVNote('保存失败：' + (e instanceof Error ? e.message : String(e))))
      .finally(() => setVSaving(false));
  };

  const keySet = (settings as any)?.model?.key_set;
  const title = NAV.flatMap((g) => g.items).find(([k]) => k === section)?.[1] ?? '设置';

  // 打磨（§5.11）：全屏层支持 Escape 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // 二十一轮 🔴2：与 TeamView 同款 inert/焦点管理
  // （二十一轮修复：此前误插进 Escape effect 回调内部 → Rules of Hooks 违规 →
  //  打开设置整站白屏；现为顶层独立 effect）
  useEffect(() => {
    const root = rootId ? document.getElementById(rootId) : null;
    const prevFocus = document.activeElement as HTMLElement | null;
    if (root) root.inert = true;
    const first = overlayRef.current?.querySelector<HTMLElement>('button, [href], input, select, textarea');
    first?.focus();
    return () => {
      if (root) root.inert = false;
      prevFocus?.focus?.();
    };
  }, [rootId]);

  // 二十六轮第 3 批第 5 处：focus trap 闭环——inert 只护住了 #root，
  // 焦点从"最后一个控件"正向 Tab 会掉到 body（独立实测：连按 40 次 Tab
  // 第 22 次落 body）。Tab 到末尾回卷首个、Shift+Tab 到首个回卷末尾；
  // 关闭后归还触发按钮由上方 cleanup 的 prevFocus 承担。
  const trapTab = (e: ReactKeyboardEvent) => {
    if (e.key !== 'Tab') return;
    const overlay = overlayRef.current;
    if (!overlay) return;
    const nodes = [...overlay.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )].filter((n) => !n.hasAttribute('disabled') && (n.offsetParent !== null || getComputedStyle(n).position === 'fixed'));
    if (nodes.length === 0) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div ref={overlayRef} role="dialog" aria-modal="true" aria-label="设置" className="settings-page" onKeyDown={trapTab}>
      <aside className="settings-nav">
        <button className="settings-back" onClick={onClose}>← 返回工作区</button>
        {NAV.map((g) => (
          <div key={g.group}>
            <div className="nav-group">{g.group}</div>
            {g.items.map(([k, label]) => (
              <button
                key={k}
                className={`nav-item ${section === k ? 'nav-on' : ''}`}
                onClick={() => setSection(k)}
              >
                {(() => {
                  const Icon = NAV_ICONS[k as string];
                  return Icon ? <Icon size={15} style={{ verticalAlign: -3, marginRight: 7 }} /> : null;
                })()}
                {label.replace(/^[^一-龥A-Za-z]+/, '')}
              </button>
            ))}
          </div>
        ))}
      </aside>

      <main className="settings-main">
        <h2 className="settings-title">
          {(() => {
            const Icon = NAV_ICONS[section as string];
            return Icon ? <Icon size={18} style={{ verticalAlign: -4, marginRight: 9 }} /> : null;
          })()}
          {title.replace(/^[^一-龥A-Za-z]+/, '')}
        </h2>

        {/* ★ 设置页正规化（Phase 3 ⑧）：每节统一的"说明 + 状态徽章 + 恢复默认"。
            徽章数据来自已加载的 settings / capabilities；恢复默认走后端白名单（危险节会被拒）。 */}
        <SectionHead
          desc={SECTION_META[section as string]?.desc ?? ''}
          status={sectionStatus()}
          tone={sectionTone()}
          onReset={RESETTABLE.includes(section as string) ? () => void doReset(section as string) : undefined}
        />

        {section === 'general' && (
          <>
          <div className="card">
            <div className="card-title">手机直连（局域网）</div>
            {/* ★ 第 8 批 问题4：用户原话「对小白来说他根本就不会设置——你写一个什么
                backend config.json，他们根本不懂、不知道搁哪找」。
                原来这里是一段 4 步手工说明，而且最后一步教人打开 5173 —— 那个端口
                vite 只绑 localhost，外部本来就进不来（说明本身就是失效的）。
                现在改成：点一下开通 → 自动生成访问密码 → 直接给出手机能打开的完整链接。 */}
            <div className="kv"><span>当前状态</span><b>{lan?.enabled ? '已开通（手机可连）' : lan?.pending_lan ? '已开通 · 等待重启生效' : '仅本机'}</b></div>

            {lan?.enabled && (
              <>
                <div className="kv"><span>手机打开</span><b className="mono small">{lan?.url}</b></div>
                {/* ★ 二维码（第 8b 批）：手机上不用手打那串 32 位访问密码 —— 扫一下就行。
                    编码用 qrcode 库，**验证**用独立的解码库 jsqr 做「编→解码」闭环
                    （见 scripts/verify_lan_qr.mjs）——不拿自己的实现验自己。 */}
                {qrDataUrl && (
                  <div style={{ marginTop: 10, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                    <img
                      src={qrDataUrl}
                      alt="手机扫码直连二维码"
                      width={168}
                      height={168}
                      style={{ borderRadius: 8, background: '#fff', padding: 6 }}
                    />
                    <div className="card-hint" style={{ maxWidth: 260 }}>
                      用手机相机或微信扫这个码，直接打开 Agent（已带访问密码，不用手打）。
                      <br />手机要和电脑连同一个 WiFi。
                    </div>
                  </div>
                )}
                <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                  <button className="btn-save" onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(lan?.url ?? '');
                      setLanCopied(true); setTimeout(() => setLanCopied(false), 1600);
                    } catch { setLanErr('复制失败——请手动选中上面的链接'); }
                  }}>{lanCopied ? '已复制 ✓' : '复制链接'}</button>
                  <button disabled={lanBusy} onClick={async () => {
                    setLanBusy(true); setLanErr('');
                    try { setLan(await api.lanDisable()); }
                    catch (e) { setLanErr(e instanceof Error ? e.message : String(e)); }
                    finally { setLanBusy(false); }
                  }}>关闭手机直连</button>
                </div>
              </>
            )}

            {!lan?.enabled && !lan?.pending_lan && (
              <>
                <div className="card-hint">
                  手机连同一个 WiFi、打开一个链接就能操作这个 Agent。点一下开通即可——
                  <b>访问密码由程序自动生成</b>，不用你去改任何配置文件。
                </div>
                <div style={{ marginTop: 8 }}>
                  <button className="btn-save" disabled={lanBusy} onClick={async () => {
                    setLanBusy(true); setLanErr('');
                    try {
                      const r = await api.lanEnable();
                      setLan(r);
                      // ★ 关键一步（本班自查出并补上）：开通局域网后，token 闸门对
                      //   **所有**路径生效（含本机）——重启后本机浏览器也会被要密码，
                      //   而那时连设置页都 401 了，用户根本看不到密码，只能回去翻
                      //   config.json（正是这个功能要消灭的事）。所以开通时顺手把它
                      //   存进本机 localStorage：重启后本机自动已登录。
                      if (r.token) {
                        try { localStorage.setItem('authToken', r.token); } catch { /* 隐私模式等 */ }
                      }
                    } catch (e) { setLanErr(e instanceof Error ? e.message : String(e)); }
                    finally { setLanBusy(false); }
                  }}>{lanBusy ? '开通中…' : '一键开通'}</button>
                </div>
              </>
            )}

            {!!lan && !lan.enabled && lan.pending_lan && (
              <div style={{ marginTop: 8 }}>
                <button disabled={lanBusy} onClick={async () => {
                  setLanBusy(true); setLanErr('');
                  try { setLan(await api.lanDisable()); }
                  catch (e) { setLanErr(e instanceof Error ? e.message : String(e)); }
                  finally { setLanBusy(false); }
                }}>取消开通</button>
              </div>
            )}

            {lan?.needs_restart && (
              <div className="card-hint">⚠️ 已写入配置，<b>需要重启后端</b>才生效：关掉后端窗口，重新运行 start.bat。</div>
            )}
            {/* ★ A-4：查看密码（仅本机）+ 改密码。后端按**真实来源 IP** 判本机，手机拿不到也改不了。 */}
            <div className="token-row">
              <button className="btn-mini" disabled={tokenBusy} onClick={() => void loadToken()}>
                {tokenBusy ? '读取中…' : '查看密码（仅本机）'}
              </button>
              {tokenShown !== null && (
                <>
                  <code className="mono token-value">{tokenShown || '（未设置）'}</code>
                  {!!tokenShown && (
                    <button
                      className="btn-mini"
                      onClick={() => {
                        void navigator.clipboard?.writeText(tokenShown).catch(() => undefined);
                        setTokenNote('已复制到剪贴板');
                      }}
                    >
                      复制
                    </button>
                  )}
                </>
              )}
              <button className="btn-mini" onClick={() => { setPwOpen((v) => !v); setPwErr(''); }}>
                {pwOpen ? '收起改密码' : '改密码'}
              </button>
            </div>
            {pwOpen && (
              <div className="token-row">
                <input
                  className="token-input"
                  type="text"
                  value={pwDraft}
                  placeholder="新密码（≥8 位，不能有空格）；留空 = 随机生成一个强的"
                  onChange={(e) => setPwDraft(e.target.value)}
                />
                <button className="btn-mini" onClick={() => setPwDraft(randomToken())}>随机生成</button>
                <button
                  className="btn-mini edit-save"
                  disabled={pwBusy}
                  onClick={() => {
                    setPwBusy(true); setPwErr('');
                    void api.setAccessToken(pwDraft.trim())
                      .then((r) => {
                        setTokenShown(r.token);
                        setTokenNote(r.note);
                        setPwDraft('');
                        setPwOpen(false);
                        void api.lanStatus().then(setLan).catch(() => undefined);   // 二维码要重新扫
                      })
                      .catch((e) => setPwErr(e instanceof Error ? e.message : String(e)))
                      .finally(() => setPwBusy(false));
                  }}
                >
                  {pwBusy ? '保存中…' : '保存新密码'}
                </button>
              </div>
            )}
            {pwErr && <div className="card-hint warn">{pwErr}</div>}
            {tokenNote && <div className="card-hint sec-note">{tokenNote}</div>}

            {/* ★ Phase 3 ⑧ 第二刀：设置导出/导入（换机器一键搬；**导出文件里没有密钥**） */}
            <div className="token-row">
              <button
                className="btn-mini"
                onClick={() => {
                  void api.exportSettings().then((data) => {
                    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                    const a = document.createElement('a');
                    a.href = URL.createObjectURL(blob);
                    a.download = `agent-shell-设置-${new Date().toISOString().slice(0, 10)}.json`;
                    a.click();
                    URL.revokeObjectURL(a.href);
                    setPortNote('已导出（文件里**没有** API Key 与访问密码）');
                  }).catch((e) => setPortNote(`导出失败：${e}`));
                }}
              >
                导出设置
              </button>
              <label className="btn-mini port-import">
                导入设置…
                <input
                  type="file"
                  accept="application/json,.json"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    e.target.value = '';                 // 允许连续导入同一个文件
                    if (!f) return;
                    setPortNote('正在读取…');
                    void f.text().then(async (txt) => {
                      try {
                        const parsed = JSON.parse(txt) as { sections?: Record<string, unknown> };
                        const sections = parsed.sections ?? (parsed as Record<string, unknown>);
                        // ★ 先预演（后端不落盘），把"会改动什么"摊给用户看，确认了再应用
                        const dry = await api.importSettings(sections, true);
                        const lines = Object.entries(dry.changed).map(([k, v]) => `${k}：${(v as string[]).join('、')}`);
                        setPortPending({ sections, summary: lines });
                        setPortNote(lines.length ? `将改动 ${lines.length} 个节` : '这个文件和当前设置一样，没有可改的');
                      } catch (err) {
                        setPortPending(null);
                        setPortNote(`导入失败：${err instanceof Error ? err.message : String(err)}`);
                      }
                    });
                  }}
                />
              </label>
              {portPending && (
                <button
                  className="btn-mini edit-save"
                  onClick={() => {
                    void api.importSettings(portPending.sections, false)
                      .then((r) => { setPortNote(r.note); setPortPending(null); load(); })
                      .catch((e) => setPortNote(`导入失败：${e}`));
                  }}
                >
                  确认导入
                </button>
              )}
            </div>
            {portPending && (
              <div className="card-hint">将改动：{portPending.summary.join('；') || '（无）'}</div>
            )}
            {portNote && <div className="card-hint sec-note">{portNote}</div>}
            {!!lan && !lan.ui_ready && (
              <div className="card-hint">⚠️ 界面还没构建：在 frontend 目录跑一次 <b>npm run build</b>，手机才打得开界面。</div>
            )}
            {(lan?.enabled || lan?.pending_lan) && (
              <div className="card-hint">⚠️ 同一 WiFi 下、知道访问密码的设备都能操控这个 Agent——别在公共 WiFi 开通。</div>
            )}
            {lanErr && <div className="card-hint" style={{ color: '#f0736a' }}>{lanErr}</div>}
          </div>
          <div className="card">
            <div className="card-title">记忆库（越用越懂你）</div>
            <div className="kv"><span>状态</span><b>{memory?.enabled ? '开启' : '关闭'}</b></div>
            <div className="kv"><span>已记住</span><b>{memory?.count ?? 0} 条</b></div>
            {((memory?.entries as any[]) ?? []).slice(-5).reverse().map((e) => (
              <div key={e.ts + e.content} className="card-hint">[{e.type}] {String(e.content).slice(0, 60)}</div>
            ))}
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button className="btn-warn" onClick={async () => {
                // ★ 2026-10-07：裸 fetch → api.*（此前这四处**绕过了统一入口** ✗ ——
                //   局域网模式下靠全局包装器兜着才没 401 ✓ 但"每个调用点都得记着带密码"这件事本身
                //   就是隐患 ✓ 见 api.ts 顶部 C12 那段注释 ✓）
                await api.setMemory({ enabled: !memory?.enabled });
                setMemory(await api.getMemory());
              }}>{memory?.enabled ? '关闭记忆' : '开启记忆'}</button>
              <button className="btn-danger" onClick={async () => {
                if (!confirm('确定清空全部长期记忆？此操作不可恢复。')) return;
                await api.setMemory({ clear: true });
                setMemory(await api.getMemory());
              }}>清空记忆</button>
            </div>
            <div className="card-hint">每次任务交付后自动提取"值得记住的事"（偏好/纠错/事实），全存本机，随时查看或清空。</div>
            {/* ★ 2026-10-07（用户："数据在你手里"✓）：**一键导出** ✓
                用 `<a download>` + `authedUrl` ✓ —— 不写 JS ✓ 浏览器原生下载 ✓
                （记忆库是最像"你的东西"的一份数据 ✓ 得能拿走 ✓） */}
            <div style={{ marginTop: 8 }}>
              <a className="btn-mini" style={{ textDecoration: 'none' }}
                href={authedUrl('/api/v1/memory/export')} download="memory-export.json">
                导出记忆库（JSON）
              </a>
              <span style={{ fontSize: 11, opacity: 0.55, marginLeft: 8 }}>
                原始内容 ✓ 已经过打码 ✓ 拿到就能读 ✓
              </span>
            </div>
          </div>
          <div className="card">
            <div className="card-title">知识库（让 Agent 引用你的文档）</div>
            {/* ★★ 2026-10-07（用户问出来的 ✗✗）：为什么知识库要用阿里百炼？
                —— "**我做这个知识库本地的，没想到弄什么通义千问的了**"
                原来这段只写着"需要阿里云百炼 Key 做向量化" ✗ —— **资料会外传**这件事
                既没说清 ✓ 也没给**本地**这条更好的路 ✗✓。
                ⇒ 现在把向量档位摆出来 ✓ 本地优先 ✓ 并**如实标注云端会把资料发出去** ✗。 */}
            {(() => {
              const kb = caps?.capabilities?.kb;
              if (!kb) return <div className="card-hint">正在读取向量档位…（功能：把文档切块、算出"意思"存本地，之后按语义检索）</div>;
              return (
                <>
                  <div className="kv">
                    <span>向量档位（"找得到"靠它）</span>
                    <b>{kb.providers[kb.current]?.label ?? kb.current}</b>
                  </div>
                  {Object.entries(kb.providers).map(([pid, p]) => {
                    const on = kb.current === pid;
                    return (
                      <div className="svc-grid" key={pid} style={{ marginBottom: 10 }}>
                        <span className="svc-name">{p.label}</span>
                        <span className="svc-state">
                          <em className={`voice-state ${p.state === 'ready' ? 'ok' : 'warn'}`}>
                            {p.state === 'ready' ? '可用' : '还差一步'}
                          </em>
                        </span>
                        <span className="svc-desc">{p.detail}</span>
                        <span className="svc-act">
                          {on
                            ? <b className="voice-cur">当前使用</b>
                            : (
                              <button className="btn-mini" disabled={kbTierBusy}
                                onClick={() => void switchKb(pid)}>用这个</button>
                            )}
                          {pid === 'local' && (
                            <button className="btn-mini" style={{ marginLeft: 6 }} disabled={kbTierBusy}
                              title="装 fastembed（轻量 ONNX，不需要 torch）；装完首次建库会自动下向量模型（约 100MB）"
                              onClick={() => void api.installKbEmbedder().catch((e) => setKbTierNote(String(e)))}>
                              一键装
                            </button>
                          )}
                        </span>
                      </div>
                    );
                  })}
                  {!!kbTierNote && <div className="card-hint">{kbTierNote}</div>}
                  <div className="card-hint">
                    本地档：完全离线 ✓ 免费 ✓ <b>资料不出本机</b> ✓（首次装依赖 + 下模型之后就永久离线）
                    　云端档：免下载 ✓ 但<b>建库时资料片段会发到阿里</b> ✗ 且按量计费
                  </div>
                </>
              );
            })()}
            {kbs.length === 0 && <div className="card-hint">还没有知识库——把一个装着文档的文件夹交给 Agent 入库（支持 md/txt/代码等文本）。</div>}
            {kbs.map((k) => (
              <div key={k.name} className="kv" style={{ alignItems: 'center' }}>
                <span><b>{k.name}</b>　<span className="card-hint" style={{ display: 'inline' }}>{k.description} · {k.chunks} 块</span></span>
                {/* ★ 一键导出（zip ✓ 含 manifest + 切块 + 说明 ✓ 见后端 kb_export 的注释 ✓） */}
                <a className="btn-mini" style={{ textDecoration: 'none', marginRight: 6 }}
                  href={authedUrl(`/api/v1/kb/${encodeURIComponent(k.name)}/export`)}
                  download={`${k.name}.zip`}>导出</a>
                <button className="btn-danger" onClick={async () => {
                  if (!confirm(`删除知识库「${k.name}」？`)) return;
                  await api.deleteKb(k.name);
                  setKbs(await api.listKbs());
                }}>删除</button>
              </div>
            ))}
            <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <input placeholder="知识库名" value={kbName} onChange={(e) => setKbName(e.target.value)} style={{ width: 120, padding: '6px 8px', border: '1px solid rgba(255,255,255,0.16)', borderRadius: 6, background: 'transparent', color: 'inherit' }} />
              <input placeholder="文档文件夹的完整路径，如 D:/docs" value={kbFolder} onChange={(e) => setKbFolder(e.target.value)} className="mono" style={{ flex: 1, minWidth: 200, padding: '6px 8px', border: '1px solid rgba(255,255,255,0.16)', borderRadius: 6, background: 'transparent', color: 'inherit' }} />
              <button className="btn-primary" disabled={kbBusy || !kbName.trim() || !kbFolder.trim()} onClick={async () => {
                setKbBusy(true);
                try {
                  // ★ 2026-10-07：原来这里手写 `r.json()` + 自己判 `r.ok` ✗ ——
                  //   与 `request()` 的规矩重复（而且它**绕过了带密码那一步** ✓）；
                  //   改成 api.* 后：非 2xx 一律抛出并带上后端 detail ✓ 与全站一致 ✓
                  await api.ingestKb(kbName.trim(), kbFolder.trim());
                  setKbName(''); setKbFolder('');
                  setKbs(await api.listKbs());
                } catch (e) { alert(e instanceof Error ? e.message : String(e)); } finally { setKbBusy(false); }
              }}>{kbBusy ? '入库中…' : '入库'}</button>
            </div>
            {/* ★ 2026-10-07：这句是**用户当场问的那句** ✗ —— "需要阿里云百炼 Key 做向量化"
                原来只说了云端那条路 ✓ 而且**没说资料会外传** ✗✓。
                ⇒ 改成如实的两档说明 ✓（本地优先 ✓ 云端要标"资料会发到阿里" ✗）。 */}
            <div className="card-hint">
              入库后 Agent 回答时会自动检索引用（带出处）。向量档位在上面的「向量档位」里选：
              <b>本地</b>（离线 ✓ 免费 ✓ 资料不出本机 ✓，首次要装依赖 + 下约 100MB 模型）/
              <b>云端阿里百炼</b>（免下载 ✓ 但建库时资料片段会发到阿里 ✗，按量计费）。
            </div>
          </div>
          <div className="card">
            <div className="card-title">语音功能用谁的服务（计费说明）</div>
            {/* ★ 2026-10-06 改准（用户实测问出来的 ✗）：
                · 这两行原来是**写死的旧文案** ✗ —— "朗读：微软 Edge" 而设置页另一处写"固定用 MeloTTS" ✗
                  ⇒ 现在**从能力总览读实际在用的那个** ✓（口径只有一处 ✓ 不会再打架 ✓）
                · 而且识别那边**漏了本地那档** ✗（本地离线也支持 ✓ 只是要自己装 ✓）
                  ⇒ 两档都写出来 ✓ 并说清"没装怎么办" ✓ */}
            <div className="kv">
              <span>语音识别（说话转文字）</span>
              <b>{caps?.capabilities?.asr
                ? (caps.capabilities.asr.providers[caps.capabilities.asr.current]?.label
                   ?? caps.capabilities.asr.current)
                : '读取中…'}</b>
            </div>
            <div className="card-hint" style={{ marginTop: -2 }}>
              两档：**云端小米 MiMo**（按音频时长计费，需小米 Key ✓）/ **本地离线**（免费 ✓ 但要自己装，装了就不用 Key ✓）
            </div>
            <div className="kv">
              <span>朗读（文字转语音）</span>
              <b>{caps?.capabilities?.tts
                ? (caps.capabilities.tts.providers[caps.capabilities.tts.current]?.label
                   ?? caps.capabilities.tts.current)
                : '读取中…'}</b>
            </div>
            <div className="card-hint" style={{ marginTop: -2 }}>
              可以换 ✓ 在上面的「语音合成（说）」那张卡里点「用这个」——没装的后端会**如实告诉你"发不出声"并给安装命令** ✓
            </div>
            <div className="card-hint">注意：大脑换成 DeepSeek/GLM 等其他模型不影响朗读；语音识别选云端那档时才需要小米 Key。</div>
          </div>
          <div className="card">
            <div className="card-title">界面</div>
            {/* ★ 2026-10-07：主题从"只读展示"改成**真能切** ✓（用户要的深色 + 浅色 ✓）
                切换会：① 立刻写到 `<html data-theme>` ✓ ② 记在 localStorage（重启后还在 ✓）
                ③ 存进后端配置（换设备也对 ✓） */}
            <div className="kv">
              <span>主题</span>
              <b>
                <button className="btn-mini" disabled={themeBusy}
                  onClick={() => void switchTheme('dark')}>深色</button>
                <button className="btn-mini" style={{ marginLeft: 6 }} disabled={themeBusy}
                  onClick={() => void switchTheme('light')}>浅色</button>
                <em className="voice-state ok" style={{ marginLeft: 8 }}>
                  当前：{theme === 'light' ? '浅色' : '深色'}
                </em>
              </b>
            </div>
            {!!themeNote && <div className="card-hint">{themeNote}</div>}
            <div className="card-hint">
              语言：{String(settings?.ui?.language ?? 'zh-CN')}（语言暂为只读 ✓ 主题已可切 ✓）
            </div>
          </div>
          </>
        )}

        {section === 'model' && (
          <>
            <div className="card">
              <div className="card-title">当前模型</div>
              <div className="kv"><span>提供者</span><b>{String(settings?.model?.provider ?? '—')}</b></div>
              <div className="kv"><span>模型</span><b>{String(settings?.model?.model_name ?? '—')}</b></div>
              <div className="kv"><span>API 地址</span><b className="mono small">{String(settings?.model?.base_url ?? '—')}</b></div>
              <div className="kv">
                <span>Key 状态</span>
                <b className={keySet === false ? 'warn' : 'ok'}>
                  {keySet === true ? '已设置' : keySet === false ? '未设置' : '— 不需要'}
                </b>
              </div>
            </div>
            {/* ★ 2026-10-06：步数上限 + 单价表（此前只能改配置文件）。
                步数上限治"代码活被砍在半路"；单价表治"花了多少钱看不见"。 */}
            <div className="card">
              <div className="card-title">干活的上限与花费</div>
              <label className="fld">
                <span>单任务最大步数</span>
                <input
                  type="number" min={1} max={500} value={maxIter}
                  onChange={(e) => setMaxIter(Number(e.target.value) || 0)}
                />
              </label>
              <div className="card-hint">
                群任务另按角色分档：写代码/测试 60 步、设计 40 步、文案 25 步。
                （代码活要"写-跑-改"反复迭代，25 步常在快做完时被砍断。）
              </div>
              <div className="card-hint" style={{ marginTop: 10 }}>
                <b>单价（元/百万 token）</b> —— 填了群里每步就报「约 ¥X」；不填只报 token。
                <b>价格会变，我这里不预填</b>（编一个"看起来对"的价会让你拿它当账单）。
              </div>
              {priceRows.map((row, i) => (
                <div className="fld fld-inline" key={i}>
                  <input placeholder="模型名（如 mimo-v2.6-flash）" value={row.model}
                    onChange={(e) => setPriceRows(priceRows.map((r, j) => j === i ? { ...r, model: e.target.value } : r))} />
                  <input type="number" step="0.1" placeholder="输入价" value={row.inp}
                    onChange={(e) => setPriceRows(priceRows.map((r, j) => j === i ? { ...r, inp: e.target.value } : r))} />
                  <input type="number" step="0.1" placeholder="输出价" value={row.out}
                    onChange={(e) => setPriceRows(priceRows.map((r, j) => j === i ? { ...r, out: e.target.value } : r))} />
                  <button className="btn-mini danger" onClick={() => setPriceRows(priceRows.filter((_, j) => j !== i))}>删</button>
                </div>
              ))}
              <div className="btn-row sticky-actions">
                <button className="btn-mini" onClick={() => setPriceRows([...priceRows, { model: '', inp: '', out: '' }])}>加一行单价</button>
                {/* ★ 2026-10-06（用户提的）："价格随时会变，不能让我手填吧？"
                    ⇒ 一键去公开价格表查（服务商自己的 API 不给价 ✗，这点在结果里说明白）✓ */}
                <button className="btn-mini" disabled={limitsBusy} onClick={() => void autoFetchPrice()}>自动查询单价（联网）</button>
                <button className="btn-mini primary" disabled={limitsBusy} onClick={() => void saveLimits()}>
                  {limitsBusy ? '保存中…' : '保存上限与单价'}
                </button>
              </div>
              {limitsMsg && <div className={'card-hint ' + (limitsOk ? 'ok' : 'warn')}>{limitsMsg}</div>}
            </div>
            {/* ★★ 2026-10-07「每个 Key 花费上限」——用户点名要的 ✓
                （他原话："每个 Key 花费上限（现在只有成本**显示** ✗ 没有**上限** ✗）"）
                ★ 这张卡片的**第一要务是说实话** ✓ 两件必须写出来的事：
                  ① 上限**只管语言模型**（唯一按 token 算得出钱的 ✓）
                  ② 出图/出视频/语音是**按次或按秒**计费，后端拿不到单价 ⇒ **拦不住** ✓
                  默默不管 = 给用户**假的安心** ✗ —— 那比不做还糟 ✓（本项目一贯口径 ✓） */}
            <div className="card">
              <div className="card-title">花费上限（每个 Key 花到多少就停）</div>
              {!budget && <div className="card-hint">正在读取…</div>}
              {budget && (
                <>
                  {budget.blocked && (
                    <div className="card-hint warn" style={{ marginBottom: 8 }}>
                      ⏸ 现在正被上限拦着（下一步会被停下，不会继续花钱）：{budget.blocked}
                    </div>
                  )}
                  {budget.keys.length === 0 && (
                    <div className="card-hint">还没有可设上限的 Key —— 先去上面「模型设置」把 Key 填上。</div>
                  )}
                  {budget.keys.map((row) => (
                    <div className="kv" key={row.key} style={{ alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
                      <span style={{ minWidth: 190 }}>
                        <b className="mono">{row.key}</b>
                        <span className="card-hint" style={{ display: 'inline', marginLeft: 6 }}>
                          {row.roles.length ? `（${row.roles.join(' / ')}）` : '（旧的用量记录，没标是哪把 Key）'}
                        </span>
                      </span>
                      <span className="card-hint">
                        已花 <b>{row.cny > 0 ? `¥${row.cny.toFixed(4)}` : '¥0'}</b>
                        {row.limit > 0 ? ` / 上限 ¥${row.limit.toFixed(2)}` : '（没设上限，不拦）'}
                        {row.calls > 0 && ` · ${row.calls} 次调用 · ${row.tokens.toLocaleString()} tok`}
                        {row.unpriced_calls > 0 && ` · ⚠ ${row.unpriced_calls} 次没填单价，没算进钱里`}
                      </span>
                      <span style={{ display: 'inline-flex', gap: 4 }}>
                        <input
                          className="mono" style={{ width: 74, padding: '4px 6px' }}
                          placeholder={row.limit > 0 ? String(row.limit) : '元'}
                          value={budgetDraft[row.key] ?? ''}
                          onChange={(e) => setBudgetDraft((m) => ({ ...m, [row.key]: e.target.value }))}
                        />
                        <button className="btn-mini" disabled={budgetBusy}
                          onClick={() => saveCap(row.key, budgetDraft[row.key])}
                          title="存这把 Key 的上限（填 0 = 撤掉上限；从现在起算）">
                          存上限
                        </button>
                        {row.limit > 0 && (
                          <button className="btn-mini" disabled={budgetBusy}
                            onClick={() => saveCap(row.key, undefined, true)}
                            title="从现在重新计（老账不删，只是不计入这个上限）">
                            清零重来
                          </button>
                        )}
                      </span>
                    </div>
                  ))}
                  <div className="card-hint" style={{ marginTop: 8 }}>{budget.note}</div>
                  <div className="card-hint">
                    怎么算的：花多少 = 上面填的**单价 × 实际 token** ✓（用同一份用量记录，不另记一本账 ✓）。
                    所以**先填单价**这项才准 ✓；没填单价的模型只报 token、不算钱 ✓（不许编数字 ✗）。
                  </div>
                  {budgetMsg && <div className="card-hint ok">{budgetMsg}</div>}
                </>
              )}
            </div>
            {/* ★★ 2026-10-07「一键清干净」——用户点名要的 ✓（他自己就标了"⚠️破坏性"✓）
                三条设计（每一条都在回答"万一用户手滑了怎么办"✓）：
                  ① **先备份再清** ✓ 不是删、是挪到 `data/_cleared_<时间>/` ✓ 可回滚 ✓
                  ② **二次确认是真确认** ✗ 要把确认词**原样打出来** ✓（点两下按钮不算 ✓）
                  ③ **说不清就不清** ✓ 逐条列出"会清什么 / **不会**碰什么" ✓
                     （设置、Key、已下模型、授权 —— 这些删了要重配重下几个 G ✓） */}
            <div className="card">
              <div className="card-title">⚠️ 一键清干净（清空我之前的数据）</div>
              {!clearPv && <div className="card-hint">正在读取…</div>}
              {clearPv && (
                <>
                  <div className="card-hint">{clearPv.note}</div>
                  {clearPv.running.length > 0 && (
                    <div className="card-hint warn" style={{ marginTop: 6 }}>
                      现在有 {clearPv.running.length} 个任务在跑 —— 先等它结束或取消才能清 ✗
                      （不然它正在写的文件会被抽走）
                    </div>
                  )}
                  {clearPv.items.map((it) => (
                    <label className="kv" key={it.key} style={{ alignItems: 'center', gap: 8, cursor: it.exists ? 'pointer' : 'default' }}>
                      <span>
                        <input
                          type="checkbox" disabled={!it.exists}
                          checked={clearPick.includes(it.key)}
                          onChange={(e) => setClearPick((m) => e.target.checked ? [...m, it.key] : m.filter((x) => x !== it.key))}
                        />{' '}
                        {it.label}
                      </span>
                      <b>{it.exists ? `${(it.bytes / 1024 / 1024).toFixed(1)} MB` : '（没有）'}</b>
                    </label>
                  ))}
                  <div className="card-hint" style={{ marginTop: 8 }}>
                    <b>不会碰</b>（删了得重配/重下，所以留着）：
                    {clearPv.keeps.map((k) => <div key={k}>· {k}</div>)}
                  </div>
                  <div className="fld" style={{ marginTop: 8 }}>
                    <span>二次确认：请把下面这句**原样**打出来 —— <b className="mono">{clearPv.phrase}</b></span>
                    <input
                      className="mono" value={clearWord} placeholder={clearPv.phrase}
                      onChange={(e) => setClearWord(e.target.value)}
                    />
                  </div>
                  <div className="btn-row sticky-actions">
                    <button
                      className="btn-mini danger" disabled={clearBusy || clearPv.running.length > 0}
                      onClick={doClear}
                      title="先备份再清空；原件会挪到 data/_cleared_<时间>/，随时能拿回来"
                    >
                      {clearBusy ? '清理中…' : `清空选中的 ${clearPick.length} 类`}
                    </button>
                  </div>
                  {clearMsg && <div className={'card-hint ' + (clearMsg.startsWith('✅') ? 'ok' : 'warn')}>{clearMsg}</div>}
                </>
              )}
            </div>
            {/* ★ 2026-10-10：浏览器实况缓存垃圾（实测教训的产物 ✓）
                每开一次浏览器会话就留一个 Chromium profile ✗ 实测本机 20+ 个 / 约 2 GB /
                1.5 万个文件 ✓ 还会被快照复制、被备份打包（备份卡死后端那次就有它一份 ✗）
                ★ 只删名字以 _edgeprof 开头的目录 ✗ 其它一个字节都不碰 ✓ */}
            <div className="card">
              <div className="card-title">🧹 浏览器缓存垃圾（可清）</div>
              <div className="card-hint">
                浏览器实况工具每开一次会话，就在任务/组的工作区里留一个 Chromium 配置目录
                （<code className="mono">_edgeprof*</code>）✗ —— 实测攒到 <b>20+ 个 / 约 2 GB</b>。
                删掉<b>不影响任何任务数据</b> ✓（下次用浏览器会重新生成一个 ✓）
              </div>
              {!bcStat && <div className="card-hint">正在读取…</div>}
              {bcStat && (
                <>
                  <div className={'card-hint ' + (bcStat.dirs ? 'warn' : 'ok')}>
                    {bcStat.dirs
                      ? `现在有 ${bcStat.dirs} 个目录 / ${bcStat.files} 个文件 / ${bcStat.mb} MB`
                      : bcStat.note}
                  </div>
                  <div className="btn-row">
                    <button
                      className="btn-mini" disabled={bcBusy || !bcStat.dirs} onClick={doCleanBrowserCache}
                      title="只删 _edgeprof* 这一类目录；正被占用的会跳过并如实告诉你"
                    >
                      {bcBusy ? '清理中…' : `清掉这 ${bcStat.dirs} 个（释放 ${bcStat.mb} MB）`}
                    </button>
                  </div>
                </>
              )}
              {bcMsg && <div className={'card-hint ' + (bcMsg.startsWith('✅') ? 'ok' : 'warn')}>{bcMsg}</div>}
            </div>
            <div className="card">
              <div className="card-title">切换提供者（选择后自动填推荐配置）</div>
              <div className="chip-row">
                {Object.entries(PRESETS).map(([name, pr]) => (
                  <button key={name} className={`preset-chip ${provider === name ? 'preset-on' : ''}`} onClick={() => pickPreset(name)} title={pr.desc}>
                    {pr.full_label}
                  </button>
                ))}
              </div>
            </div>
            <div className="card">
              <div className="card-title">该提供方简介</div>
              <div className="card-hint">{PRESETS[provider]?.desc ?? '—'}</div>
              <div className="card-hint">可用模型：{PRESETS[provider]?.models ?? '—'}（更多模型名以官网为准，可直接填）</div>
            </div>
            <div className="card">
              <div className="card-title">配置</div>
              <label className="fld"><span>模型名</span><input value={modelName} onChange={(e) => setModelName(e.target.value)} /></label>
              {/* ★ Phase 2 ⑥：模型名下拉（真去问服务商）+ 一键刷新。
                  拉不到不是错误：退回内置预设，并把原因写在下面（source 说真话）。 */}
              <div className="fld fld-inline">
                <select
                  className="model-select"
                  value={modelOpts.some((o) => o === modelName) ? modelName : ''}
                  onChange={(e) => { if (e.target.value) setModelName(e.target.value); }}
                >
                  <option value="">
                    {modelOpts.length
                      ? `选择模型（${modelOpts.length} 个可选）`
                      : (modelSource === 'preset'
                          ? '拉不到在线列表（原因见下面那行）—— 可直接手填，或点右边重拉'
                          : '选择模型（正在拉取…）')}
                  </option>
                  {modelOpts.map((mo) => <option key={mo} value={mo}>{mo}</option>)}
                </select>
                <button className="btn-mini" disabled={modelsBusy} onClick={() => void loadModels(true)}>
                  {modelsBusy ? '拉取中…' : '刷新列表'}
                </button>
              </div>
              {modelNote && (
                <div className={`card-hint model-source ${modelSource === 'live' ? 'ok' : 'warn'}`}>{modelNote}</div>
              )}
              <label className="fld"><span>API 地址</span><input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></label>
              <label className="fld"><span>Key 存放位置（只读）</span><input value={keyEnv} readOnly title="这是内部标识，由服务商决定；改成只读，是为了防止换服务商时把 Key 存错格子" placeholder="DEEPSEEK_API_KEY" /></label>
              <label className="fld"><span>API Key（不落盘）</span><input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={keySet ? '已设置（输入可覆盖）' : '粘贴你的 Key'} /></label>
              {/* ★ Phase 1 ①：默认勾上 —— 只写进程的 Key 一重启就没了（"发消息不回复"血案）。
                  写的是 User 级环境变量（HKCU\Environment），不是 config.json。 */}
              <label className="fld fld-check" title="写进 Windows 用户级环境变量（HKCU\Environment）：免管理员、不写进配置文件，重启后端后仍然有效">
                <input type="checkbox" checked={persistKey} onChange={(e) => setPersistKey(e.target.checked)} />
                <span>同时写入用户级环境变量（推荐：重启后仍然有效，不用每次重填）</span>
              </label>
              <div className="card-hint">Key 只进环境变量，不进 config.json；保存后按提示重启一次后端（<code className="mono">restart-backend.ps1</code>）即可长期生效。</div>
              <div className="btn-row sticky-actions">
                <button className="btn-save" disabled={saving || !provider} onClick={saveModel}>{saving ? '保存中…' : '保存模型配置'}</button>
                {note && <div className="card-hint sec-note">{note}</div>}
              </div>
            </div>
          </>
        )}

        {/* ★ 2026-10-06（用户提的）：**能力总览做成表格** ——
            一眼看清 5 个能力（对话/出图/出视频/听/说）现在用谁、能不能用、备选是谁 ✓ */}
        {section === 'capabilities' && (
          <>
            <div className="card">
              <div className="card-title">能力总览</div>
              {/* ★ 2026-10-07（用户提的"能力总览加一句总答案"✓）：
                  表格再好也得先给一句**人话结论** ✓ —— 他要的是"我到底能不能用"✓
                  ⇒ 这里**动态**算一句：几项能用 / 还差几项 / 差在哪 ✓ */}
              {(() => {
                const all = caps?.capabilities;
                if (!all) return <div className="card-hint">正在读取能力状态…</div>;
                const rows = Object.entries(all);
                const bad = rows.filter(([, v]) => {
                  const cur = v.current || '';
                  return !(cur && v.providers[cur]?.state === 'ready');
                });
                return (
                  <div className="card-hint" style={{ marginBottom: 6 }}>
                    <b>
                      {bad.length === 0
                        ? `✅ 一共 ${rows.length} 项能力，现在全都能用 ✓`
                        : `现在 ${rows.length - bad.length}/${rows.length} 项能用；` +
                          `还差 ${bad.length} 项：` +
                          bad.map(([, v]) => {
                            const cur = v.current || '';
                            const st = cur ? v.providers[cur]?.state : 'unset';
                            return `${v.label}（${st === 'needs_key' ? '缺 Key'
                              : st === 'not_installed' ? '还没装' : '没选'}）`;
                          }).join('、')}
                    </b>
                    {bad.length > 0 && (
                      <div style={{ marginTop: 4, opacity: 0.8 }}>
                        怎么补：去对应那一节（语音 / 出图出视频 / 模型设置）点「一键装」或「用这个」✓
                        每行下面都写了**具体怎么办** ✓
                      </div>
                    )}
                  </div>
                );
              })()}
              <div className="card-hint">
                这张表回答一个问题：**我现在这套到底能不能出声 / 出图 / 出视频** ✓
                （状态从"声明 + 当前配置 + 环境变量"现算；Key 只显示设没设，不回传值 ✓）
              </div>
              {(() => {
                const caps5 = caps?.capabilities;
                if (!caps5) return <div className="card-hint">正在读取能力状态…</div>;
                const zh = (s?: string) => ({
                  ready: '✅ 可用', needs_key: '🔑 缺 Key', not_installed: '⬇ 未安装',
                  unknown: '❔ 未知', unset: '▫ 未选择',
                } as Record<string, string>)[s ?? ''] ?? (s ?? '—');
                const rows = Object.entries(caps5).map(([key, c]) => {
                  const cap = c as {
                    label?: string; current?: string; current_state?: string;
                    providers?: Record<string, { label?: string; state?: string; kind?: string }>;
                    fallbacks?: string[]; next_available?: string | null;
                  };
                  const provs = Object.entries(cap.providers ?? {});
                  const cur = provs.find(([pid]) => pid === cap.current);
                  const alt = provs.filter(([pid]) => pid !== cap.current && (provs.length ? true : false))
                    .map(([pid, p]) => `${p.label ?? pid}${p.state === 'ready' ? '' : `(${zh(p.state)})`}`);
                  return {
                    key,
                    label: cap.label ?? key,
                    current: cur ? (cur[1].label ?? cur[0]) : (cap.current ?? '—'),
                    state: cap.current_state ?? (cur ? cur[1].state : undefined),
                    alt: alt.slice(0, 3).join('、') || '—',
                  };
                });
                return (
                  <table className="caps-table">
                    <thead>
                      <tr><th>能力</th><th>现在用谁</th><th>状态</th><th>备选（点上面那张卡里的「用这个」才算切过去）</th></tr>
                    </thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.key}>
                          <td>{r.label}</td>
                          <td>{r.current}</td>
                          <td>{zh(r.state)}</td>
                          <td className="dim">{r.alt}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                );
              })()}
              {caps?.local_recommendation && (
                <div className="card-hint" style={{ marginTop: 10 }}>
                  本地档位推荐：<b>{caps.local_recommendation.label}</b>（{caps.local_recommendation.reason}）
                </div>
              )}
            </div>
            <div className="card">
              <div className="card-hint">
                想换某个能力的提供者 ⇒ 到「<b>语音</b>」（听/说）或「<b>视频生成</b>」「<b>模型设置</b>」里切换 ✓
              </div>
            </div>
          </>
        )}

        {section === 'voice' && (
          <>
            <div className="card">
              <div className="card-title">语音转文字（听）—— 两档并列</div>
              {(() => {
                const asr = caps?.capabilities?.asr;
                if (!asr) return <div className="card-hint">正在读取能力状态…</div>;
                const badge = (s: string) => (s === 'ready' ? 'ok' : s === 'not_installed' ? 'warn' : 'warn');
                const zh = (s: string) => ({
                  ready: '可用', needs_key: '缺 Key', not_installed: '未安装',
                  unknown: '未知', unset: '未选择',
                } as Record<string, string>)[s] ?? s;
                return (
                  <>
                    {Object.entries(asr.providers).map(([pid, p]) => (
                      // ★ 2026-10-07：改用 **Fragment** 包一层 ✓ ——
                      //   本班实测：把"一键装"那块塞进 `.voice-row` 里（它是个网格 ✗）并用
                      //   `grid-column: 1 / -1` ⇒ 按钮被推到 **x=1741**（视口才 1500）✗
                      //   **整块跑到屏幕右边外面** ✓ 截图上看就是"一片空白 + 按钮不见了" ✓。
                      //   ⇒ 现在它是 `.voice-row` 的**兄弟节点**（卡片自己的块级流 ✓ 必然整行铺满 ✓）。
                      <Fragment key={pid}>
                        {/* ★ 2026-10-07：跟语音合成那边统一成**同一个四列网格** ✓
                            （用户："他们几个都不在一排"✓ —— 两栏用同一套布局才叫整齐 ✓） */}
                        <div className="svc-grid" style={{ marginBottom: 8 }}>
                          <span className="svc-name">{p.label}</span>
                          <span className="svc-state">
                            <em className={`voice-state ${badge(p.state)}`}>{zh(p.state)}</em>
                          </span>
                          <span className="svc-desc">{p.detail}</span>
                          <span className="svc-act">
                            {asr.current === pid
                              ? <b className="voice-cur">当前使用</b>
                              : (
                                <button className="btn-mini" disabled={asrBusy} onClick={() => void switchAsr(pid)}>
                                  用这个
                                </button>
                              )}
                          </span>
                        </div>
                        {/* ★ 2026-10-07「一键装 + 一键下模型」——用户原话：
                            *"我想要的就是一键能安装，然后还能使用这种的"* ✓
                            · 装/下都在**后端**跑（跑的是写死的命令 ✓ 不接受用户输入 ✓）
                            · 这里只**轮询状态** ✓ 把**真实输出**贴出来 ✓（装没装成一目了然 ✓ 不装作成功 ✗） */}
                        {pid === 'local_qwen3' && (
                          <div className="card-hint" style={{ marginTop: 2 }}>
                            <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                              <button className="btn-mini" disabled={asrRunning}
                                title="装官方依赖包 qwen-asr（命令是写死的；装完还会再探一次确认真能 import）"
                                onClick={startInstall}>① 一键装依赖</button>
                              <select value={asrTier} onChange={(e) => setAsrTier(e.target.value)}
                                style={{ fontSize: 12 }} disabled={asrRunning}>
                                <option value="0.6b">0.6B（约 2G 显存 · 快 · 中文够用）</option>
                                <option value="1.7b">1.7B（约 4G 显存 · 中文最准）</option>
                              </select>
                              <button className="btn-mini" disabled={asrRunning}
                                title="提前把模型拉下来（0.6B 约 1-2GB / 1.7B 约 3-4GB）；不点也行，第一次转写会自动下"
                                onClick={() => startPull(asrTier)}>② 一键下模型</button>
                              {asrRunning && (
                                <span style={{ opacity: 0.85 }}>
                                  进行中…
                                  {asrJob.bytes > 0 ? ` 已下 ${(asrJob.bytes / 1048576).toFixed(1)} MB` : ''}
                                  {asrJob.elapsed > 0 ? ` · ${asrJob.elapsed}s` : ''}
                                </span>
                              )}
                            </span>
                            {!!asrJob.lines.length && (
                              <pre style={{ margin: '6px 0 0', padding: 8, background: 'rgba(0,0,0,0.28)',
                                            borderRadius: 8, fontSize: 11, maxHeight: 150,
                                            overflow: 'auto', whiteSpace: 'pre-wrap' }}>
                                {asrJob.lines.slice(-12).join('\n')}
                              </pre>
                            )}
                            <div style={{ marginTop: 4, opacity: 0.65 }}>
                              ⚠️ 先说清代价：装依赖会**连带安装 PyTorch 等大包**（视机器而定，可能几个 GB）；
                              下模型另算（0.6B 约 1–2GB / 1.7B 约 3–4GB）。
                              都不影响"云端 MiMo"那档 ✓ —— 你不点它就什么都不下 ✓。
                            </div>
                          </div>
                        )}
                      </Fragment>
                    ))}
                    {asrNote && <div className="card-hint voice-note">{asrNote}</div>}
                    {caps?.local_recommendation && (
                      <div className="card-hint">
                        本地档位推荐：<b>{caps.local_recommendation.label}</b>
                        （{caps.local_recommendation.reason}；
                        探测：{caps.local_recommendation.method}
                        {caps.local_recommendation.detected
                          ? `，显存 ${caps.local_recommendation.vram_gb}G / 内存 ${caps.local_recommendation.ram_gb}G`
                          : ''}）
                      </div>
                    )}
                  </>
                );
              })()}
            </div>
            <div className="card">
              <div className="card-title">语音合成（说）</div>
              {(() => {
                const tts = caps?.capabilities?.tts;
                if (!tts) return <div className="card-hint">正在读取能力状态…</div>;
                return (
                  <>
                    {/* ★★ 2026-10-06（用户实测问出来的 ✗✗）：
                        "他这些本地的软件……如果他这些都没有可以装吗？" ——
                        以前这里只列"可用/未安装" ✓ **该装什么、多大、能不能离线一个字没有** ✗；
                        而且下面那句还写着"切换还没做、固定用 MeloTTS" ✗（既过时又和实际不符 ✓）。
                        ⇒ 现在：**能切了** ✓ 每个后端把"怎么装 / 多大 / 能不能离线"直接摆出来 ✓
                        （那句话就是用户当场要的答案 ✓ 不用他去翻文档 ✓） */}
                    {Object.entries(tts.providers).map(([pid, p]) => {
                      const on = tts.current === pid;
                      const ready = p.state === 'ready';
                      // ★★ 2026-10-07（用户提了两次 ✗✗）："语音合成这块太不整齐了 ——
                      //   还没装、用这个、可用、用这个，他们几个都不在一排" ✓
                      //   原来每行是两列 `.kv` ✗ ⇒ 状态与按钮都挤在右列 ✓ 说明文字再另起一行 ✓
                      //   ⇒ **每行的按钮位置都不一样** ✓ 看着就是乱 ✓。
                      //   现在固定四列（名称 │ 状态 │ 说明 │ 操作 ✓ 操作列右对齐 ✓）
                      //   ⇒ 三行的「用这个」**竖着对齐** ✓ 说明文字**从同一列开始** ✓。
                      return (
                        <div className="svc-grid" key={pid} style={{ marginBottom: 10 }}>
                          <span className="svc-name">{p.label}</span>
                          <span className="svc-state">
                            <em className={`voice-state ${ready ? 'ok' : 'warn'}`}>
                              {ready ? '可用' : '还没装'}
                            </em>
                          </span>
                          <span className="svc-desc">{p.detail}</span>
                          {/* ★ 「当前使用」放**操作列** ✓ —— 放名称列会把名称挤到第二行 ✗
                              （截图当场看到的 ✓）而且跟"听"那边的排法一致 ✓ */}
                          <span className="svc-act">
                            {/* ★★ 2026-10-08：**没装的那两档直接给「一键装」** ✓
                                （用户："就像上面这个千问3似的，点一下它就安装" ✓）
                                · 只有真装了才按钮变「用这个」✓ 没装就给装的路 ✓
                                · 装的动作在**后端**跑（写死的命令 ✓ 不接受用户输入 ✓）
                                · 进度走同一个状态接口 ✓ 块里贴**真实输出** ✓ 不装作成功 ✓
                                · **同一时刻只跑一个**（与 ASR 共用一把锁 ✓ 两条 pip 撞一起会互相毁掉 ✓）*/}
                            {/* ★★ 2026-10-08 补：**「当前使用」与「一键装」要能同时显示** ✓
                                —— 否则"正在用一个没装的"那一行只剩按钮 ✓ 看不出选的是哪个 ✗
                                  （这是我自己改出来的小毛病 ✓ 当场补上 ✓）*/}
                            {on && <b className="voice-cur">当前使用</b>}
                            {!ready && (pid === 'melotts' || pid === 'qwen3tts') ? (
                              <button className="btn-mini primary" disabled={asrRunning}
                                title={`一键装「${p.label}」（命令是写死的；装完会再探一次确认真能用）`}
                                onClick={() => void startTtsInstall(pid)}>
                                {asrRunning && asrJob.kind === 'install-tts' ? '安装中…' : '一键装'}
                              </button>
                            ) : (!on && (
                              <button className="btn-mini" disabled={ttsBusy}
                                onClick={() => void switchTts(pid)}>用这个</button>
                            ))}
                          </span>
                        </div>
                      );
                    })}
                    {!!ttsNote && <div className="card-hint">{ttsNote}</div>}
                    {/* ★★ 2026-10-08：**音色选择器**（用户："音色能不能自己挑" ✓）
                        · 只有**带 voices 的后端**才显示（千问3 有 9 个 ✓ edge/pyttsx3 没有 ✓）
                        · 中文音色标出来 ✓ —— 免得挑到日韩音色去念中文 ✓
                        · 选了立刻生效并**如实回报**（失败就显示后端的原话 ✓ 不装作成功 ✗）*/}
                    {(() => {
                      const cur = tts.providers[tts.current] as
                        { voices?: { id: string; zh?: boolean }[] } | undefined;
                      const vs = cur?.voices ?? [];
                      if (!vs.length) return null;
                      return (
                        <div className="kv" style={{ marginTop: 6 }}>
                          <span>音色</span>
                          <select
                            value={ttsVoice || vs[0].id}
                            disabled={ttsBusy}
                            title="千问3-TTS 自带 9 个音色；标「中文」的更适合念中文"
                            onChange={(e) => {
                              const v = e.target.value;
                              setTtsBusy(true); setTtsNote('');
                              void api.setTts(tts.current, v)
                                .then((r) => { setTtsVoice(v); setTtsNote(r.note || `音色已切到 ${v}`); })
                                .catch((err) => setTtsNote(`切音色失败：${err instanceof Error ? err.message : String(err)}`))
                                .finally(() => setTtsBusy(false));
                            }}
                          >
                            {vs.map((v) => (
                              <option key={v.id} value={v.id}>
                                {v.id}{v.zh ? '（中文）' : ''}
                              </option>
                            ))}
                          </select>
                        </div>
                      );
                    })()}
                    {/* ★ 2026-10-08：**装的过程贴在这儿**（真实输出 ✓ 连已下多少 MB 都有 ✓）
                        —— 只在"这次装的是朗读引擎"时显示 ✓ 别把 ASR 的输出串到这张卡里 ✗ */}
                    {asrJob.kind === 'install-tts' && !!asrJob.lines.length && (
                      <div className="card-hint">
                        {asrRunning && (
                          <div style={{ marginBottom: 4 }}>
                            安装中…{asrJob.bytes > 0 ? ` 已下 ${(asrJob.bytes / 1048576).toFixed(1)} MB` : ''}
                            {asrJob.elapsed > 0 ? ` · ${asrJob.elapsed}s` : ''}
                          </div>
                        )}
                        <pre style={{ margin: 0, padding: 8, background: 'rgba(0,0,0,0.28)',
                                      borderRadius: 8, fontSize: 11, maxHeight: 160,
                                      overflow: 'auto', whiteSpace: 'pre-wrap' }}>
                          {asrJob.lines.slice(-14).join('\n')}
                        </pre>
                      </div>
                    )}
                    <div className="card-hint">
                      没装的后端**先选也行** ✓ 但它会如实告诉你"现在还发不出声、怎么装" ✓
                      —— 本项目不装作能用 ✓
                      <br />★ 本地的两档都能点「**一键装**」：
                      <b>千问3-TTS</b>（**中文最准** ✓ 2.4GB ✓ Apache-2.0 可商用 ✓）、
                      <b>MeloTTS</b>（约 100MB 模型，依赖要拖 torch）。
                      <br />（原先还有一档 **Kokoro** ✓ 已**下架** ✗ —— 它 20 多个中文音色
                      全把「本地」念成「喷嚏」✓ 实测 ✓ 不摆了 ✓）
                    </div>
                  </>
                );
              })()}
            </div>
          </>
        )}

        {section === 'video' && (
          <>
            {/* ★ 2026-10-06（用户提的）：出图与出视频**放在同一节**看 ✓
                —— 这两个能力常常一起用（先出图再拿图去出视频 ✓），
                   以前出图的状态只在能力表里能看到，切来切去很别扭 ✗ */}
            {(() => {
              const img = caps?.capabilities?.image as {
                label?: string; current?: string; current_state?: string;
                providers?: Record<string, { label?: string; state?: string; detail?: string }>;
              } | undefined;
              const zh = (s?: string) => ({
                ready: '✅ 可用', needs_key: '🔑 缺 Key', not_installed: '⬇ 未安装',
                unknown: '❔ 未知', unset: '▫ 未选择',
              } as Record<string, string>)[s ?? ''] ?? (s ?? '—');
              if (!img) return null;
              const provs = Object.entries(img.providers ?? {});
              return (
                <div className="card">
                  <div className="card-title">当前出图引擎</div>
                  {/* ★★ 2026-10-07（体检⑥ 引出来的真缺口 ✗）：**出图这一档此前在界面上切不了** ✗
                      —— 能力表里明明写着两档 ✓ 而其余每一档（听/说/知识库）都有「用这个」✓
                      唯独出图要**手改 config.json** ✗；更糟的是下面那句提示还写着
                      "到模型设置里改" ✗ —— 而那里根本没有这一项 ✗（指到一个不存在的地方 ✓）
                      ⇒ 现在把开关放在**它该在的地方**（出图这一页 ✓）并把那句错提示改掉 ✓ */}
                  {provs.map(([pid, p]) => (
                    <div className="svc-grid" key={pid} style={{ marginBottom: 8 }}>
                      <span className="svc-name">{p.label ?? pid}</span>
                      <span className="svc-state"><em className={`voice-state ${p.state === 'ready' ? 'ok' : 'warn'}`}>{zh(p.state)}</em></span>
                      <span className="card-hint" style={{ display: 'inline' }}>{p.detail}</span>
                      <span>
                        {pid === img.current ? (
                          <button className="btn-mini" disabled>正在用</button>
                        ) : (
                          <button
                            className="btn-mini primary" disabled={imgBusy}
                            title="切到这个引擎（校验过才写；切完立刻生效 ✓）"
                            onClick={() => {
                              setImgBusy(true); setImgMsg('');
                              void api.setImage(pid)
                                .then((r) => { setImgMsg(r.note); load(); loadCaps(); })
                                .catch((e) => setImgMsg(`切换失败：${e instanceof Error ? e.message : String(e)}`))
                                .finally(() => setImgBusy(false));
                            }}
                          >用这个</button>
                        )}
                      </span>
                    </div>
                  ))}
                  {imgMsg && <div className="card-hint ok">{imgMsg}</div>}
                  <div className="card-hint">
                    <b>云端通义万相</b>：要 Key、按量计费（额度用完会报错 —— 那是账号的事，不是软件的事 ✓）<br />
                    <b>本地 ComfyUI</b>：离线 ✓ 不花钱 ✓ 但要**先把 ComfyUI 起起来** ✓
                    （状态那一栏会真去探它有没有在跑 ✓ 不是在跑就直接告诉你 ✓）
                  </div>
                </div>
              );
            })()}
            <div className="card">
              <div className="card-title">当前视频引擎</div>
              <div className="kv"><span>引擎</span><b>{String((settings as any)?.video?.provider || '— 未启用（用本地 ComfyUI）')}</b></div>
              <div className="kv"><span>模型</span><b>{String((settings as any)?.video?.model ?? '—')}</b></div>
              <div className="kv"><span>档位</span><b>{String((settings as any)?.video?.resolution ?? '—')}</b></div>
              <div className="kv">
                <span>Key 状态</span>
                <b className={(settings as any)?.video?.key_set === false ? 'warn' : 'ok'}>
                  {(settings as any)?.video?.key_set === true ? '已设置' : (settings as any)?.video?.key_set === false ? '未设置' : '— 未配置'}
                </b>
              </div>
            </div>
            <div className="card">
              <div className="card-title">选择引擎（点选自动填推荐配置）</div>
              <div className="chip-row">
                {Object.entries(VIDEO_PRESETS).map(([name, p]) => (
                  <button key={name} className={`preset-chip ${vProvider === name ? 'preset-on' : ''}`} onClick={() => pickVideoPreset(name)}>
                    {p.label}
                  </button>
                ))}
                <button className={`preset-chip ${vProvider === '' ? 'preset-on' : ''}`} onClick={() => { setVProvider(''); setVModel(''); setVKeyEnv(''); }}>
                  不启用
                </button>
              </div>
              <div className="card-hint">对话里说"用海螺生成…"会自动切到对应引擎（需在下方配好该引擎的 Key）。</div>
            </div>
            <div className="card">
              <div className="card-title">配置</div>
              <label className="fld"><span>模型名</span><input value={vModel} onChange={(e) => setVModel(e.target.value)} placeholder="wan3.0-video" /></label>
              <label className="fld"><span>分辨率档位（按所选引擎显示该家的档位与官方单价）</span>
                <select value={resOptions.some((o) => o.value === vRes) ? vRes : resOptions[0].value} onChange={(e) => setVRes(e.target.value)}>
                  {resOptions.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </label>
              <div className="card-hint">价格为官方标准价（元/秒 × 时长 = 单条费用），以控制台实时价为准；未标价的档位以服务商控制台为准。</div>
              <label className="fld"><span>Key 环境变量名</span><input value={vKeyEnv} onChange={(e) => setVKeyEnv(e.target.value)} placeholder="DASHSCOPE_API_KEY" /></label>
              <label className="fld"><span>API Key（不落盘）</span><input type="password" value={vApiKey} onChange={(e) => setVApiKey(e.target.value)} placeholder={(settings as any)?.video?.key_set ? '已设置（输入可覆盖）' : '粘贴你的 Key'} /></label>
              <button className="btn-save" disabled={vSaving || !vProvider} onClick={saveVideo}>{vSaving ? '保存中…' : '保存视频配置'}</button>
              {vNote && <div className="card-hint">{vNote}</div>}
            </div>
            <div className="card">
              <div className="card-title">多引擎 Key（可选——配了就能在对话里点名切换）</div>
              {['minimax', 'seedance', 'kling'].map((name) => {
                const cur = (settings as any)?.video?.engines?.[name];
                const draft = engDraft[name] ?? { keyEnv: cur?.api_key_env ?? '', apiKey: '' };
                return (
                  <div key={name} className="fld-group">
                    <div className="card-title" style={{ fontSize: 12 }}>
                      {VIDEO_PRESETS[name]?.label ?? name}
                      <b className={cur?.key_set ? 'ok' : 'warn'} style={{ marginLeft: 8 }}>
                        {cur?.key_set ? '已设置' : '未设置'}
                      </b>
                    </div>
                    <label className="fld"><span>Key 环境变量名</span>
                      <input
                        value={draft.keyEnv}
                        placeholder={VIDEO_PRESETS[name]?.key_env ?? ''}
                        onChange={(e) => setEngDraft((m) => ({ ...m, [name]: { ...draft, keyEnv: e.target.value } }))}
                      />
                    </label>
                    <label className="fld"><span>API Key（不落盘）</span>
                      <input
                        type="password"
                        value={draft.apiKey}
                        placeholder={cur?.key_set ? '已设置（输入可覆盖）' : '粘贴 Key（留空则只改变量名）'}
                        onChange={(e) => setEngDraft((m) => ({ ...m, [name]: { ...draft, apiKey: e.target.value } }))}
                      />
                    </label>
                  </div>
                );
              })}
              <div className="card-hint">清空某引擎的环境变量名并保存 = 取消该引擎。 wan 是主引擎（上方配置），这里配其余三家。</div>
            </div>
          </>
        )}

        {section === 'executor' && (
          <>
            {/* ★ 2026-10-06「这类以后都别问」的清单与管理 ——
                审批卡片上那个按钮的提示写着"随时能在设置里收回" ✓ 这里就是那个口子 ✓
                （实测：一个 10 分钟的小活要点 2–5 次审批 ⇒ 等审批常常比干活还久 ✗） */}
            <div className="card">
              <div className="card-title">
                以后都别问的命令
                <span className="muted small" style={{ marginLeft: 8 }}>
                  （审批卡片上点过「这类以后都别问」的程序 ✓ 点 × 就收回 ✓）
                </span>
              </div>
              {Object.keys(foreverRules).length === 0 ? (
                <div className="muted small">
                  还没有。审批卡片上有个「这类以后都别问」的按钮 ——
                  点它，同一个程序（如 python / pytest / git）以后新任务也不再问你 ✓
                  <br />
                  （删除、移动、覆盖写，以及 $()、管道这类命令永远不会被它放行 ✓）
                </div>
              ) : (
                <>
                  <div className="chips" style={{ marginBottom: 8 }}>
                    {Object.entries(foreverRules).map(([k, when]) => (
                      <span className="chip" key={k} title={`记于 ${when}`}>
                        <b className="mono">{k}</b>
                        <button className="chip-x" disabled={!!foreverBusy}
                          onClick={() => void revokeForever(k)}>×</button>
                      </span>
                    ))}
                  </div>
                  <button className="btn-mini" disabled={!!foreverBusy}
                    onClick={() => void revokeForever('')}>全部收回</button>
                </>
              )}
            </div>
            <div className="card">
              <div className="card-title">当前执行环境</div>
              <div className="kv"><span>执行器</span><b>{String(settings?.executor?.type ?? 'local')}</b></div>
              <div className="kv"><span>工作区根</span><b className="mono small">{String(settings?.executor?.workspace_root ?? '—')}</b></div>
              <div className="kv"><span>授权目录</span><b className="mono small">{((settings?.executor?.allowed_dirs as string[]) ?? []).join('　｜　') || '—'}</b></div>
              <div className="kv"><span>超时</span><b>{String(settings?.executor?.timeout_seconds ?? '—')} 秒</b></div>
              <div className="kv"><span>Shell</span><b className="mono small">{String(settings?.executor?.shell ?? '默认')}</b></div>
              <div className="kv">
                <span>沙箱隔离</span>
                <b>
                  {String(settings?.executor?.sandbox ?? 'off') === 'docker' ? 'Docker 容器（命令隔离执行）' : '关（命令直接在本机跑，改动真实生效）'}
                  {settings?.executor?.sandbox_available
                    ? ' ｜ Docker: 可用'
                    : ' ｜ Docker: 不可用（启动 Docker Desktop 后可开）'}
                </b>
              </div>
              {/* A3（审计台账 P1）：宿主模式此前【没有任何风险告知】。
                  这里给的是"常驻显式提示"——只要沙箱是关的就在状态卡里出现，
                  不必等用户点开编辑器或踩到审批才发现命令是真的在本机跑。
                  判据/回滚实验见 backend/tests/test_host_mode_notice.py。 */}
              {String(settings?.executor?.sandbox ?? 'off') !== 'docker' && (
                <div className="host-exec-warn" role="alert">
                  <b>⚠ 沙箱已关闭：Agent 的命令会在你的真实电脑上执行</b>
                  <div className="card-hint">
                    这不是模拟——删除、改写、安装软件、联网请求都会真实作用于这台电脑，且<b>不可撤销</b>。
                    危险命令（rm / del / format 等）仍会先弹审批，但审批一旦放行就直接执行：
                    <b>「总是允许」= 给这条命令发一张长期通行证</b>。
                    只有确实需要访问本机磁盘/桌面（磁盘分析、文件整理）时才建议关闭；其余情况建议开启沙箱。
                  </div>
                </div>
              )}
            </div>
            <div className="card">
              <div className="card-title">编辑（保存后需重启后端生效）</div>
              {/* ★ 高风险项说明（Phase 3 ⑧ 收尾）：只补**真正没说明**的两条 ——
                  沙箱/Key/局域网此前已有明确提示（不重复造，免得设置页又变乱）。
                  纯展示：不发请求、不改值，见 components/RiskNote.tsx 顶部说明。 */}
              <label className="fld"><span>授权目录（每行一个）</span><textarea value={dirsText} onChange={(e) => setDirsText(e.target.value)} /></label>
              <RiskNote
                level={dirsRisk().level}
                summary={dirsRisk().summary}
                detail={(
                  <>
                    <div><b>它控制什么</b>：Agent 能读写的目录范围。工作区根（above）永远可写，这里额外放开别的目录。</div>
                    <div><b>改大了会怎样</b>：加 <code className="mono">C:\</code> 或整个用户目录 = <b>全盘可读写</b> —— 它能改你的文档、配置、其它项目的代码，删除也是真的删（回收站里都没有）。</div>
                    <div><b>建议</b>：只加真正要用的那一个子目录（例如 <code className="mono">C:\Users\你\Desktop\整理</code>），别加盘符根目录。</div>
                    <div><b>怎么退回</b>：把这里改回只有工作区，或点本页上方「恢复默认」。</div>
                  </>
                )}
              />
              <label className="fld"><span>需审批动作（逗号分隔）</span><input value={approvalText} onChange={(e) => setApprovalText(e.target.value)} /></label>
              <RiskNote
                level={approvalRisk().level}
                summary={approvalRisk().summary}
                detail={(
                  <>
                    <div><b>它控制什么</b>：哪些命令**必须先弹窗问你**才能执行。清单里没写的命令，Agent 直接跑。</div>
                    <div><b>清空的后果</b>：<b>所有命令都不再问你</b> —— 包含删除、格式化、改注册表。<b>不会有任何报错</b>，你只会事后发现东西没了。</div>
                    <div><b>为什么"总是允许"也要小心</b>：审批弹窗里的「总是允许」= 给那条命令发一张长期通行证，清单就形同虚设。</div>
                    <div><b>建议</b>：至少保留删除类（rm / del / rmdir / rd / erase / format / reg / remove-item）。</div>
                    <div><b>怎么退回</b>：把删除类命令加回来，或点本页上方「恢复默认」。</div>
                  </>
                )}
              />
              <label className="fld"><span>命令超时（秒）</span><input value={timeoutText} onChange={(e) => setTimeoutText(e.target.value)} style={{ maxWidth: 120 }} /></label>
              <div className="card" style={{ background: 'rgba(107,163,245,0.06)' }}>
                <div className="card-title">沙箱是什么（大白话）</div>
                <div className="card-hint"><b>开启 =</b> 给 Agent 一个隔离的虚拟工作间：它的一切操作都在"虚拟小房间"里进行，弄不坏你的电脑，但也看不到你电脑上的任何文件——C 盘/D 盘/桌面统统不可见，文件整理、磁盘分析这类任务做不了。适合：写代码、处理你主动放进工作区的文件。</div>
                <div className="card-hint"><b>关闭 =</b> Agent 直接在你的真实电脑上工作：可以访问你授权的文件夹和磁盘（分析 C 盘、整理桌面这类任务需要关闭沙箱）；危险命令仍会先弹出请求你批准。随时可以把沙箱开回来。</div>
              </div>
              <label className="fld">
                <span>沙箱隔离（shell 命令进 Docker 容器，隔离执行、免审批；需 Docker 可用）</span>
                <select value={sandboxMode} onChange={(e) => setSandboxMode(e.target.value)}>
                  <option value="off">关——直接在本机执行（默认；命令真实作用于本机，有风险）</option>
                  <option value="docker">开——Docker 容器内执行（断网、限资源、非 root）</option>
                </select>
              </label>
              <label className="fld"><span>沙箱镜像（首次自动拉取）</span><input value={sandboxImg} onChange={(e) => setSandboxImg(e.target.value)} className="mono" style={{ maxWidth: 240 }} /></label>
              <div className="card-hint">沙箱下命令只挂载任务工作区（/w），写不了桌面等外部目录；Docker 不可用时命令不执行（不静默回退本机）。</div>
              <label className="fld">
                <span>SearXNG 搜索实例（可选，填 http(s):// 地址；留空用 Bing）</span>
                <input
                  value={searxngText}
                  onChange={(e) => setSearxngText(e.target.value)}
                  placeholder="http://127.0.0.1:8080"
                  className="mono"
                />
              </label>
              <div className="card-hint">配置后联网搜索优先走 SearXNG（结果更稳），实例不可用时自动回退 Bing。</div>
              <button className="btn-save" disabled={exSaving} onClick={() => void saveExecutor()}>{exSaving ? '保存中…' : '保存执行环境'}</button>
              {exNote && <div className="card-hint">{exNote}</div>}
            </div>
          </>
        )}

        {section === 'skills' && (
          <div className="card">
            <div className="card-title">技能库（{skills.length} 个）—— 模型按需自动加载</div>
            {skills.map((s) => (
              <div key={s.name} className="list-item">
                <div className="li-name"><Puzzle size={13} style={{ display: "inline", verticalAlign: -2, marginRight: 5 }} />{s.name}</div>
                <div className="li-desc">{s.description}</div>
              </div>
            ))}
            <div className="card-hint">新增技能：在 backend/skills/ 下建子目录放 SKILL.md 即可。</div>
          </div>
        )}

        {section === 'projects' && (
          <>
            <div className="card">
              <div className="card-title">项目（{projects.length} 个）—— master 指令自动注入挂载的任务</div>
              {projects.length === 0 && (
                <div className="card-hint" style={{ padding: '6px 0 10px' }}>
                  项目 = 一段常驻的「master 指令」。挂在项目上建任务，Agent 每次都带着这段指令工作——适合固定规范（如"所有输出用中文、先查再写"）。用下方表单创建第一个。
                </div>
              )}
              {projects.map((p) => (
                <div key={p.id} className="list-item">
                  <div className="li-row">
                    <span className="li-name"><FolderOpen size={13} style={{ display: "inline", verticalAlign: -2, marginRight: 5 }} />{p.name}</span>
                    <button className="li-del" aria-label={`删除项目 ${p.name}`} title={`删除项目 ${p.name}`} onClick={() => void api.deleteProject(p.id).then(() => void api.listProjects().then(setProjects))}>✕</button>
                  </div>
                  {p.master_prompt && <div className="li-desc">{p.master_prompt}</div>}
                </div>
              ))}
            </div>
            <div className="card">
              <div className="card-title">新建项目</div>
              <label className="fld"><span>项目名</span><input value={npName} onChange={(e) => setNpName(e.target.value)} /></label>
              <label className="fld"><span>master 指令</span><textarea value={npPrompt} onChange={(e) => setNpPrompt(e.target.value)} placeholder="如：所有回复用中文；先搜索再动手…" /></label>
              <button className="btn-save" disabled={!npName.trim()} onClick={() => void api.createProject(npName.trim(), npPrompt.trim()).then(() => { setNpName(''); setNpPrompt(''); void api.listProjects().then(setProjects); })}>创建项目</button>
            </div>
          </>
        )}

        {section === 'automations' && (
          <>
            <div className="card">
              <div className="card-title">自动化（{autos.length} 个）—— 定时 / Webhook 触发</div>
              {autos.length === 0 && (
                <div className="card-hint" style={{ padding: '6px 0 10px' }}>
                  还没有自动化。定时自动化到点自动创建任务；Webhook 自动化给你一个 URL，外部系统（CI、cron、脚本）一触发就起任务。先用下面的表单建一个。
                </div>
              )}
              {autos.map((a) => (
                <div key={a.id} className="list-item">
                  <div className="li-row">
                    <span className="li-name"><Clock size={13} style={{ display: "inline", verticalAlign: -2, marginRight: 5 }} />{a.name}（{a.kind === 'hook' ? 'Webhook' : '定时'}）</span>
                    <span>
                      <button className="li-del" aria-label={a.enabled ? `暂停自动化 ${a.name}` : `启动自动化 ${a.name}`} title={a.enabled ? `暂停自动化 ${a.name}` : `启动自动化 ${a.name}`} onClick={() => void api.toggleAutomation(a.id).then(() => void api.listAutomations().then(setAutos))}>{a.enabled ? '⏸' : '▶'}</button>
                      <button className="li-del" aria-label={`删除自动化 ${a.name}`} title={`删除自动化 ${a.name}`} onClick={() => void api.deleteAutomation(a.id).then(() => void api.listAutomations().then(setAutos))}>✕</button>
                    </span>
                  </div>
                  <div className="li-desc">{a.task_input}</div>
                  {a.kind === 'hook' && (
                    <div style={{ marginTop: 6, fontSize: 11.5 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        <code style={{ background: 'rgba(255,255,255,0.05)', padding: '3px 8px', borderRadius: 6, wordBreak: 'break-all' }}>
                          POST {location.origin}/api/v1/hooks/{a.id}/{hookSecrets[a.id] || '（点「显示 URL」获取）'}
                        </code>
                        <button className="li-del" style={{ opacity: 0.8 }} onClick={() => {
                          if (hookSecrets[a.id]) { void navigator.clipboard.writeText(`${location.origin}/api/v1/hooks/${a.id}/${hookSecrets[a.id]}`); setHookCopied((s) => ({ ...s, [a.id]: true })); setTimeout(() => setHookCopied((s) => ({ ...s, [a.id]: false })), 1500); return; }
                          const v = prompt('粘贴创建时保存的 secret 以显示完整 URL（或点「轮换」生成新的）：');
                          if (v) setHookSecrets((s) => ({ ...s, [a.id]: v }));
                        }}>{hookSecrets[a.id] ? (hookCopied[a.id] ? '已复制' : '复制 URL') : '显示 URL'}</button>
                        <button className="li-del" style={{ opacity: 0.8 }} title="生成新 secret，旧 URL 立即失效" onClick={() => {
                          if (!confirm('轮换后旧 webhook URL 立即失效，使用它的外部系统需要换新地址。继续？')) return;
                          void api.rotateWebhookSecret(a.id).then((r) => {
                            setHookSecrets((s) => ({ ...s, [a.id]: r.secret }));
                            void navigator.clipboard.writeText(`${location.origin}/api/v1/hooks/${a.id}/${r.secret}`).catch(() => undefined);
                          });
                        }}>轮换</button>
                      </div>
                      <div className="card-hint">安全：URL 含密钥，泄露就点「轮换」。外部系统可带 X-AgentShell-Timestamp + X-AgentShell-Sign 头（HMAC-SHA256(secret, "时间戳.请求体")）防重放；连错 5 次会限流 1 分钟。</div>
                    </div>
                  )}
                </div>
              ))}
            </div>
            <div className="card">
              <div className="card-title">新建自动化</div>
              <label className="fld"><span>名称</span><input value={naName} onChange={(e) => setNaName(e.target.value)} /></label>
              <label className="fld"><span>每次执行的任务</span><textarea value={naInput} onChange={(e) => setNaInput(e.target.value)} /></label>
              {/* ★ 2026-10-07（第 7 项 ④）**任务描述指引** ✓ —— 原来这个框光秃秃 ✗
                  而自动化最要紧的就是**这句话写得好不好** ✓（写虚了 = 每天跑一次废话 ✓
                  而且它还会自己开任务、花你的钱 ✓）⇒ 把"好描述长什么样"直接写在这儿 ✓ */}
              <div className="card-hint">
                <b>这句话怎么写才好用</b>（自动化会**照这句自己开任务**，所以它越具体越省事 ✓）：
                <div>· ✅ <b>动作 + 对象 + 标准</b>：「把 <span className="mono">D:\Downloads</span> 里的图片按年月分到子文件夹，产出 <span className="mono">manifest.md</span>」</div>
                <div>· ✅ <b>写清"做完什么样算完"</b>（自动化没人盯着，验收标准就是它自己的刹车 ✓）</div>
                <div>· ✗ 别写「整理一下下载文件夹」这种 —— 每次跑出来的结果都不一样 ✓ 你也不知道它对不对 ✓</div>
              </div>
              {/* ★ 2026-10-06 加「文件夹变动」：用户真正每天会用的那件事（下载完就整理 ✓）
                  本质是文件变动 ✓ —— 不需要邮箱配置、不需要外部服务，本地看一眼就够 ✓ */}
              <label className="fld"><span>什么时候跑</span>
                <select value={naTrigger} onChange={(e) => setNaTrigger(e.target.value)}>
                  <option value="daily">每天定时</option>
                  {/* ★ 2026-10-07（第 7 项 ③）：这三种原来**表达不出来** ✗
                      （只有"每隔 N 分钟"和"每天几点"✓ 拿 interval 凑"每 60 分钟"
                        和"每小时整点"**不是一回事** ✓ —— 重启/晚点开跑就永远错开 ✓）*/}
                  <option value="hourly">每小时（第几分）</option>
                  <option value="weekly">每周（周几 + 几点）</option>
                  <option value="monthly">每月（几号 + 几点）</option>
                  <option value="interval">每隔 N 分钟</option>
                  <option value="watch">某个文件夹有变化时</option>
                </select>
              </label>
              {naTrigger === 'daily' && (
                <label className="fld"><span>每天几点执行</span><input type="time" value={naTime} onChange={(e) => setNaTime(e.target.value)} style={{ maxWidth: 140 }} /></label>
              )}
              {/* ★ 2026-10-07（第 7 项）：**每小时 / 每周 / 每月**（原来只有"每隔 N 分钟"和"每天几点"✗
                  用户想表达"每小时整点""每周一早上""每月 1 号"—— 一个都建不出来 ✓）*/}
              {naTrigger === 'hourly' && (
                <label className="fld"><span>每小时的第几分执行</span>
                  <input type="time" value={naTime} onChange={(e) => setNaTime(e.target.value)} style={{ maxWidth: 140 }} />
                  <span className="card-hint" style={{ display: 'inline', marginLeft: 8 }}>
                    （只看"分"这一栏 ✓ 比如 00:30 = 每小时的第 30 分 ✓）
                  </span>
                </label>
              )}
              {naTrigger === 'weekly' && (
                <>
                  <label className="fld"><span>每周几</span>
                    <select value={naWeekday} onChange={(e) => setNaWeekday(Number(e.target.value))} style={{ maxWidth: 160 }}>
                      {[['1', '周一'], ['2', '周二'], ['3', '周三'], ['4', '周四'], ['5', '周五'], ['6', '周六'], ['7', '周日']].map(([v, t]) => (
                        <option key={v} value={v}>{t}</option>
                      ))}
                    </select>
                  </label>
                  <label className="fld"><span>几点执行</span><input type="time" value={naTime} onChange={(e) => setNaTime(e.target.value)} style={{ maxWidth: 140 }} /></label>
                </>
              )}
              {naTrigger === 'monthly' && (
                <>
                  <label className="fld"><span>每月几号</span>
                    <input type="number" min={1} max={31} value={naMonthDay}
                      onChange={(e) => setNaMonthDay(Number(e.target.value) || 1)} style={{ maxWidth: 120 }} />
                    <span className="card-hint" style={{ display: 'inline', marginLeft: 8 }}>
                      （填 29–31 时，短月按**月末**算 ✓ 不会跳过不跑 ✓）
                    </span>
                  </label>
                  <label className="fld"><span>几点执行</span><input type="time" value={naTime} onChange={(e) => setNaTime(e.target.value)} style={{ maxWidth: 140 }} /></label>
                </>
              )}
              {naTrigger === 'interval' && (
                <label className="fld"><span>间隔（分钟）</span><input type="number" min={1} value={naMinutes} onChange={(e) => setNaMinutes(Number(e.target.value) || 60)} style={{ maxWidth: 120 }} /></label>
              )}
              {naTrigger !== 'watch' && (
                <>
                  {/* ★ 2026-10-07（第 7 项）：**单次花费上限** + **失败自动重试** ✓
                      （与"每个 Key 上限"是两把锁：那把管总量 ✓ 这把管"某一次跑飞了"✓）*/}
                  <label className="fld"><span>这一次最多花多少钱（元，留空 = 不限）</span>
                    <input type="number" min={0} step="0.1" value={naMaxCost} placeholder="例如 1.5"
                      onChange={(e) => setNaMaxCost(e.target.value)} style={{ maxWidth: 140 }} />
                  </label>
                  <label className="fld"><span>跑失败了自动重试几次（0–5，留空 = 不重试）</span>
                    <input type="number" min={0} max={5} value={naRetries}
                      onChange={(e) => setNaRetries(e.target.value)} style={{ maxWidth: 120 }} />
                  </label>
                </>
              )}
              {naTrigger === 'watch' && (
                <>
                  <label className="fld"><span>盯着哪个文件夹</span>
                    <input value={naWatchPath} placeholder="例如 D:\Downloads"
                      onChange={(e) => setNaWatchPath(e.target.value)} />
                  </label>
                  <label className="fld"><span>两次触发至少隔（秒）</span>
                    <input type="number" min={10} value={naWatchSec} style={{ maxWidth: 120 }}
                      onChange={(e) => setNaWatchSec(Number(e.target.value) || 60)} />
                  </label>
                  <div className="card-hint">
                    文件夹里**新出现或刚改过**文件就开跑 ✓（只看这一层，不递归 ✓）。<br />
                    **建好那一刻不算变化** ✓（不会一建就立刻跑一次 ✓）；路径不存在会当场报错 ✓（免得你以为它在盯 ✗）。
                  </div>
                </>
              )}
              <button className="btn-save"
                disabled={!naName.trim() || !naInput.trim() || (naTrigger === 'watch' && !naWatchPath.trim())}
                onClick={() => void api.createAutomation(
                  naName.trim(),
                  naTrigger === 'watch' ? 'watch' : 'schedule',
                  naInput.trim(),
                  naTrigger === 'daily' ? { kind: 'daily', time: naTime }
                    : naTrigger === 'hourly' ? { kind: 'hourly', time: naTime }
                      : naTrigger === 'weekly' ? { kind: 'weekly', weekday: naWeekday, time: naTime }
                        : naTrigger === 'monthly' ? { kind: 'monthly', day: naMonthDay, time: naTime }
                          : naTrigger === 'interval' ? { kind: 'interval', minutes: naMinutes } : null,
                  naTrigger === 'watch' ? { watchPath: naWatchPath.trim(), watchSeconds: naWatchSec } : undefined,
                  // ★ 2026-10-07（第 7 项）：留空 ⇒ 不带这两个字段 ⇒ 后端按"不拦/不重试"✓
                  {
                    maxCostCny: naMaxCost.trim() ? Number(naMaxCost) : undefined,
                    retries: naRetries.trim() ? Number(naRetries) : undefined,
                  },
                ).then(() => {
                  setNaName(''); setNaInput(''); setNaWatchPath('');
                  void api.listAutomations().then(setAutos);
                }).catch((e) => setNaErr(e instanceof Error ? e.message : String(e)))}>创建</button>
              {!!naErr && <div className="err small">{naErr}</div>}
            </div>
          </>
        )}

        {section === 'stats' && (
          <>
            {/* ★★ 2026-10-07（第 9 项）**跨任务审计流水** ✓ ——
                回答"这一个月 Agent 让我批过哪些命令、我批了什么、它为什么停过"✓
                （原来这些只能一个个任务点进去翻 ✓ 202 个任务谁翻得动 ✓）
                ★ 只记"需要有人负责"的 ✓ 不记对话内容与工具输出 ✗（那是任务事件流的活 ✓）*/}
            <div className="card">
              <div className="card-title">🧾 审计流水（跨任务）</div>
              <div className="card-hint">
                这里记的是**需要有人负责**的那几件事：你批过哪些命令 ✓ 放开过哪些「以后都别问」✓
                被花费上限/角色限权挡下过什么 ✓ 以及任务起止 ✓。
                <br />**不记**对话内容和工具输出 ✗（那些在各自任务里 ✓）；
                命令里的密钥**已经打码** ✓。只留最近 {audit?.max_entries ?? 2000} 条 ✓。
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '8px 0' }}>
                <button className="btn-mini" onClick={() => void loadAudit()}>刷新</button>
                <select value={auditKind} onChange={(e) => { setAuditKind(e.target.value); void loadAudit(e.target.value); }}
                  style={{ maxWidth: 180 }}>
                  <option value="">全部</option>
                  {/* ★★ 2026-10-07 自查抓出（我自己留的洞 ✗）：
                      这里原来还列了「放权/收回」「任务起止」两项 ✓ 而后端**从来不写这两种 kind** ✗
                      ⇒ 选它们**永远是空的** ✓ 而界面会说"还没有记录"✗ —— 那是**假话** ✓
                        （不是"没有记录"✓ 是"根本没有这种东西"✓）
                      ⇒ 只有**真会写**的两种才配出现在筛选里 ✓
                        （「以后都别问」的放权确实记了 ✓ 但它记在 `approval` 里 ✓
                          带 `decision=forever` ✓ 选「审批决议」就能看到 ✓ 不需要单独一项 ✓）*/}
                  <option value="approval">审批决议</option>
                  <option value="blocked">被闸门挡下</option>
                  {/* ★ 2026-10-07：**清空数据也要留痕** ✓ —— 用户当天清完 202 → 4 个任务 ✓
                      而账本里一个字都没有 ✗ ⇒ 事后说不清"数据是什么时候没的、挪哪去了" ✓
                      （这条记着**备份路径** ✓ 所以它同时是"找回东西的线索" ✓）*/}
                  <option value="cleared">清空数据</option>
                  {/* ★★ 2026-10-07 补一小步（**我自己漏的** ✗）：
                      账本**早就在写** `deleted_task` 了 ✓（带 task_id ✓ 字节数 ✓ 挪到哪去了 ✓）
                      可界面**没给这一项** ⇒ 用户**筛不出来** ✗ —— 记了等于白记 ✓
                      （他只会在「全部」里被别的条目淹掉 ✓ 而"我删的那个任务去哪了"正是要找的 ✓）
                      ★ 与上面那条纪律是一体两面：**能筛的必须真会写** ✓ **真写了的也必须能筛** ✓ */}
                  <option value="deleted_task">删任务</option>
                  {/* ★★ A-4（2026-10-07）：**任务起止** ✓ 一项一条 `task` + `phase`（start/done）✓
                      **不拆两个 kind** ✗（拆了这里就得开两个选项 ✓ 两边还得一直对齐 ✓）
                      这一项此前被我删掉过 ✓（那会儿后端真不写 ✓ 留着就是骗人 ✗）；现在真会写了 ✓ */}
                  <option value="task">任务起止</option>
                  {/* ★ 2026-10-08：**回收站清理** ✓ —— 这是最后一个"记了却筛不出来"的 kind ✓
                      （`_prune_trash` 每次删任务顺手清旧的回收站目录 ✓ 也记一笔账 ✓
                        真删掉东西必须留痕 ✓ 那就得让用户查得到 ✓）*/}
                  <option value="trash_pruned">回收站清理</option>
                </select>
                {audit && <span className="card-hint">共 {audit.count} 条 ｜ {Object.entries(audit.by_kind).map(([k, v]) => `${k}:${v}`).join('  ')}</span>}
              </div>
              {auditErr && <div className="err small">{auditErr}</div>}
              <div style={{ maxHeight: 280, overflow: 'auto', fontFamily: 'monospace', fontSize: 12 }}>
                {!audit || audit.entries.length === 0
                  ? <div className="card-hint">还没有记录 ✓（第一次审批 / 第一次被挡下之后就会出现 ✓）</div>
                  : audit.entries.map((e, i) => (
                    <div key={i} style={{ padding: '3px 0', borderBottom: '1px solid var(--line, #eee)' }}>
                      <span style={{ opacity: 0.6 }}>{String(e.ts)}</span>{' '}
                      <b>{String(e.kind)}</b>{' '}
                      {Object.entries(e).filter(([k]) => k !== 'ts' && k !== 'kind')
                        .map(([k, v]) => `${k}=${String(v).slice(0, 120)}`).join('  ')}
                    </div>
                  ))}
              </div>
            </div>

            {/* 汇总卡 */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 8, marginBottom: 14 }}>
              {[
                { label: '总 Token', val: (usage?.total_tokens ?? 0).toLocaleString() },
                { label: '输入', val: (usage?.input_tokens ?? 0).toLocaleString() },
                { label: '缓存命中', val: (usage?.cached_tokens ?? 0) > 0 ? (usage?.cached_tokens ?? 0).toLocaleString() : '—' },
                { label: '输出', val: (usage?.output_tokens ?? 0).toLocaleString() },
                { label: 'LLM 调用', val: (usage?.calls ?? 0).toLocaleString() + ' 次' },
                { label: '覆盖任务', val: `${usage?.tasks_with_usage ?? 0}/${usage?.task_count ?? 0}` },
              ].map((s) => (
                <div key={s.label} style={{ background: 'rgba(255,255,255,0.05)', borderRadius: 8, padding: '10px 12px', textAlign: 'center' }}>
                  <div style={{ fontSize: 11, opacity: 0.55, marginBottom: 4 }}>{s.label}</div>
                  <div style={{ fontSize: 15, fontWeight: 700 }}>{s.val}</div>
                </div>
              ))}
            </div>
            {/* ★★ 2026-10-07（第 1 项"统计口径三条"查出来的 ✓）：**把话说明白** ✓
                用户看到"总 Token 几千万"第一反应是"烧了多少钱" ✗ ——
                而实测里 **92.8% 的输入是缓存命中**（¥0.02/百万，正常是 ¥1.00/百万 ✓）
                ⇒ 3596 万 tok 实际只花了约 **¥5.76**（没缓存的话要 ¥37 ✓）。
                不写这一句，用户就会被那个数字吓到（而那是**看错了**）✗ */}
            {(usage?.cached_tokens ?? 0) > 0 && (
              <div className="card-hint" style={{ marginBottom: 12 }}>
                ★ 输入里的 <b>{(usage?.cached_tokens ?? 0).toLocaleString()}</b> tok 是
                <b>缓存命中</b>（占输入
                {' '}{(((usage?.cached_tokens ?? 0) / Math.max(1, usage?.input_tokens ?? 1)) * 100).toFixed(1)}%）
                —— 这部分**按折扣价计费**（通常是正常输入价的 1/50 左右）✓
                <br />
                ⇒ 所以「总 Token」这个数**看着大、实际不贵** ✓ 要算钱请看上面的单价 × 各段用量，
                或者群里每步报的那行「约 ¥X」✓
              </div>
            )}
            {/* 按模型统计表 */}
            {usage?.by_model && Object.keys(usage.by_model).length > 0 && (
              <div className="card" style={{ marginBottom: 14 }}>
                <div className="card-title">按模型统计</div>
                <table style={{ width: '100%', fontSize: 12.5, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left', opacity: 0.6 }}>
                      <th style={{ padding: '6px 8px' }}>模型</th>
                      <th style={{ padding: '6px 8px', textAlign: 'right' }}>输入 tok</th>
                      <th style={{ padding: '6px 8px', textAlign: 'right' }}>输出 tok</th>
                      <th style={{ padding: '6px 8px', textAlign: 'right' }}>缓存命中</th>
                      <th style={{ padding: '6px 8px', textAlign: 'right' }}>调用</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(usage.by_model as Record<string, { input: number; output: number; calls: number; cached?: number }>).map(([m, v]) => (
                      <tr key={m} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                        <td style={{ padding: '6px 8px' }} className="mono small">{m}</td>
                        <td style={{ padding: '6px 8px', textAlign: 'right' }}>{v.input.toLocaleString()}</td>
                        <td style={{ padding: '6px 8px', textAlign: 'right' }}>{v.output.toLocaleString()}</td>
                        <td style={{ padding: '6px 8px', textAlign: 'right' }}>{(v.cached ?? 0) > 0 ? (v.cached ?? 0).toLocaleString() : '—'}</td>
                        <td style={{ padding: '6px 8px', textAlign: 'right' }}>{v.calls.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {/* 明细卡 */}
            <div className="card">
              <div className="card-title">数据</div>
              <div className="kv"><span>覆盖任务</span><b>{usage?.tasks_with_usage ?? 0} / {usage?.task_count ?? 0}</b></div>
              <div className="kv"><span>任务总数</span><b>{String(settings?.stats?.tasks ?? '—')}</b></div>
              <div className="kv"><span>数据目录</span><b className="mono small">{String(settings?.stats?.data_dir ?? '—')}</b></div>
            </div>
            {/* Top 消耗任务 */}
            {usage?.by_task && (usage.by_task as any[]).length > 0 && (
              <div className="card">
                <div className="card-title">Top 消耗任务</div>
                {(usage.by_task as any[]).slice(0, 10).map((t, i) => (
                  <div key={t.task_id ?? i} className="kv">
                    {/* 二十六轮 5-2/第4批第2处：title 用脱敏后的 full_label 并附
                        task_id——同前缀任务（首条消息本就相同）靠 id 必可区分 */}
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 280 }} title={`${(t as any).full_label ?? ''}（${t.task_id}）`}>{i + 1}. {t.label}</span>
                    <b>{(t.total_tokens ?? 0).toLocaleString()} tok</b>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {section === 'about' && (
          <>
            <div className="card">
              <div className="card-title">授权管理</div>
              {/* ★★ 2026-10-09：**AGPL 版与商用版必须一眼分得清** ✗→✓
                  为什么（不是文案洁癖 ✓）：本仓自 2026-10-08 晚起是 **AGPL-3.0** ✓
                  而 AGPL 第 10 条**禁止附加任何进一步限制** ✗ ——
                  「试用期 30 天 · 到期不许创建任务」+「未经授权不得用于商业用途」
                  这两句**本身就是限制的暗示** ✗ ⇒ 公开版**不能显示它们** ✓
                  ⇒ `lic.enforced === false`（AGPL 版 ✓ 默认 ✓）就显示"开源版·无使用限制" ✓
                  ⇒ 商用版（`enforce = True` ✓）才显示试用期 ✓ = 双授权分得干净 ✓ */}
              {lic && lic.enforced === false ? (
                <div className="li-desc">
                  📖 <b>开源版</b>（{lic.license || 'AGPL-3.0-only'}）· <b>无使用限制</b> ——
                  你可以自由使用、修改、分发，**包括商用** ✓
                  <br />
                  <span style={{ opacity: 0.75 }}>
                    条件是：改了并通过网络对外提供服务时，需按 AGPL 开源你的修改；
                    不想开源可购商业授权（见仓库里的 COMMERCIAL.md）。
                  </span>
                  {!!lic.source_url && (
                    <>
                      <br />源码：<b>{lic.source_url}</b>
                      {/* ★ AGPL 第 13 条：用网络访问本程序的人必须能拿到源码 ✓
                          所以这一行**必须在界面上** ✓ 只写在 README 里不够 ✓ */}
                    </>
                  )}
                  {/* ★★ 2026-10-09：**反馈入口**（用户点名的待办 ✓）
                      为什么放 Gitee 而不是 GitHub ✗：国内访问 Gitee 又稳又快 ✓
                      GitHub 经常连不上（今天实测过好几次 ✗）⇒ 反馈入口必须放**用户点得开**的那个 ✓
                      ★ 而且这对项目本身也有用 ✓：真实用户的问题 = 最真实的"落地证据" ✓ */}
                  <br />遇到问题 / 想提建议：<b>gitee.com/yangbo0801/lanternlogic-agent/issues</b>
                  <span style={{ opacity: 0.75 }}>
                    （国内打开快 ✓ GitHub 同名仓库也有 ✓ 两个都行 ✓ 提之前先搜一下有没有人提过 ✓）
                  </span>
                </div>
              ) : lic && lic.activated ? (
                <div className="li-desc">
                  ✅ <b>{lic.kind}</b>{lic.name && ` — ${lic.name}`}{lic.exp && `（有效期至 ${lic.exp}）`}
                </div>
              ) : lic && (
                <div className="li-desc">
                  当前：<b>试用期</b>
                  {lic.trial_expired
                    ? '（已到期——创建新任务需录入授权；历史数据仍可查看导出）'
                    : `（剩余 ${lic.trial_days_left} 天）`}，个人/单一团队内部使用，禁止转售与对外提供服务。
                </div>
              )}
              <label className="fld"><span>License key</span><input value={licKey} onChange={(e) => setLicKey(e.target.value)} placeholder="ASL1.xxxx.xxxx（购买后由卖家提供）" /></label>
              <button className="btn-save" disabled={!licKey.trim()} onClick={() => void api.activateLicense(licKey.trim()).then((r) => { setLicMsg(r.message); setLicKey(''); void api.getLicense().then(setLic); }).catch((e) => setLicMsg(e instanceof Error ? e.message : String(e)))}>激活授权</button>
              {licMsg && <div className="card-hint" style={{ marginTop: 6 }}>{licMsg}</div>}
            </div>
            <div className="card">
              {/* ★★ 2026-10-07（第 3 项）：版本号**不再硬编码** ✗ ——
                  加之前这里是写死的「LanternLogic Agent v0.1.0」✓ 而 `package.json` 另有一份 ✓
                  **后端压根没有** ✗ ⇒ 三处各说各的（改一处另两处不动 ✓ 本项目栽过五次的老坑 ✓）
                  ⇒ 现在显示**后端报的**（唯一来源 `backend/app/version.py` ✓）
                  并且在这儿就能**查有没有新版本** ✓（只查、不装 ✓ 没配地址就如实说没查 ✓） */}
              <div className="card-title">
                {ver ? `${ver.name} v${ver.version}` : '版本（正在读…）'}
              </div>
              <div className="li-desc">本地运行的自主智能体 · 事件流驱动 · 七层接口可替换 · MCP 插件生态</div>
              {ver?.vendor && <div className="card-hint">出品：{ver.vendor}</div>}
              <div className="btn-row sticky-actions">
                <button
                  className="btn-mini" disabled={updBusy}
                  title="只查有没有新版本 —— 查到也只告诉你去哪下，不会自动替换你的程序"
                  onClick={() => {
                    setUpdBusy(true); setUpdMsg('');
                    void api.checkUpdate()
                      .then((r) => setUpd(r))
                      .catch((e) => setUpdMsg(`检查失败：${e instanceof Error ? e.message : String(e)}`))
                      .finally(() => setUpdBusy(false));
                  }}
                >{updBusy ? '正在检查…' : '检查更新'}</button>
              </div>
              {upd && (
                <div className={'card-hint ' + (upd.has_update ? 'warn' : '')}>
                  {upd.has_update
                    ? <>🎉 {upd.note}
                        {/* ★ 这个地址是**从远端 JSON 来的** ✗ ⇒ 必须过协议白名单 ✓
                            （不然对方返回一个 `javascript:…` 就能在你点的时候执行脚本 ✓
                               —— 正是守卫测试 `test_no_other_bare_external_href` 拦我的那一下 ✓
                                  它拦得对 ✓ 我没去改测试，改的是自己这行 ✓）*/}
                        {safeHref(upd.url) && <><br />下载地址：<a href={safeHref(upd.url)!} target="_blank" rel="noreferrer">{upd.url}</a></>}
                        {upd.notes && <><br />更新说明：{upd.notes}</>}</>
                    : upd.note}
                </div>
              )}
              {updMsg && <div className="card-hint warn">{updMsg}</div>}

              {/* ★★ 2026-10-08：**作者卡**（只读 + 防伪签名 ✓ 用户定的内容 ✓）
                  · 主名只写**工作室** ✓（用户原话："真名你就写我工作室，非得写我名干啥" ✓）
                  · 邮箱/微信**默认不显示** ✓ 点一下才展开 ✓（界面清爽 ✓ 顺带少被爬 ✓）
                  · 「✅ 正版」必须来自**后端验签结论** ✓ —— 不许写死 ✗
                    （红绿有一条实验专门钉这个：把 verified 写死 true ⇒ 必红 ✓）
                  · 笔名放**底部小字**当彩蛋 ✓（用户给的"漫天炫舞大呲花" ✓）*/}
              {auth && (
                <div className="author-card" style={{ marginTop: 10, paddingTop: 10,
                                                      borderTop: '1px dashed var(--border, #334)' }}>
                  <div className="kv">
                    <span>👤 作者</span>
                    <b>{auth.org}</b>
                  </div>
                  {!!auth.line && <div className="li-desc">「{auth.line}」</div>}
                  {authOpen ? (
                    <>
                      {!!auth.email && (
                        <div className="kv"><span>✉ 邮箱</span><b>{auth.email}</b></div>
                      )}
                      {!!auth.wechat && (
                        <div className="kv"><span>💬 微信</span><b>{auth.wechat}</b></div>
                      )}
                      <div className="card-hint">
                        {auth.verified
                          ? <>✅ 正版作者卡（签名已验证 ✓ · 指纹 <code>{auth.fingerprint}</code>
                              {auth.signed_at && <> · 签于 {auth.signed_at}</>}）</>
                          : <>⚠️ <b>这张卡不是原版</b>：{auth.reason || '验签没通过'}
                              <br />（内容被改过，或者这不是官方发布的版本 ✓）</>}
                      </div>
                      {!!auth.alias && (
                        <div className="card-hint" style={{ opacity: 0.6, fontSize: 11 }}>
                          （江湖人称：{auth.alias}）
                        </div>
                      )}
                      <button className="btn-mini" style={{ marginTop: 6 }}
                        onClick={() => setAuthOpen(false)}>收起 ▲</button>
                    </>
                  ) : (
                    <button className="btn-mini" style={{ marginTop: 6 }}
                      title="联系方式默认不摆在页面上（点开才显示，少被爬虫抓走）"
                      onClick={() => setAuthOpen(true)}>展开 ▾</button>
                  )}
                </div>
              )}
            </div>
            <div className="card">
              <div className="card-title">核心能力</div>
              <div className="kv"><span>大脑</span><b>10 家 BYOK（DeepSeek / 通义 / GLM / Kimi / MiMo / Claude / Ollama 本地…）</b></div>
              <div className="kv"><span>记忆库</span><b>自动提取 · 全本地存储 · 越用越懂你</b></div>
              <div className="kv"><span>知识库</span><b>文档入库 · 语义检索 · 回答带出处</b></div>
              <div className="kv"><span>语音识别</span><b>两档：云端 MiMo ASR（按时长计费）/ 本地离线（免费，需自己装）</b></div>
              {/* ★ 2026-10-06：朗读**读实际在用的那个** ✓（原来写死 Edge ✗ 与别处口径打架 ✓） */}
              <div className="kv"><span>朗读</span><b>
                {caps?.capabilities?.tts
                  ? `${caps.capabilities.tts.providers[caps.capabilities.tts.current]?.label ?? caps.capabilities.tts.current}（可在设置里换）`
                  : 'Edge / MeloTTS / 系统语音（可选，可在设置里换）'}
              </b></div>
              <div className="kv"><span>图像生成</span><b>通义万相（云端，开箱即用）+ ComfyUI 本地可选</b></div>
              <div className="kv"><span>视频生成</span><b>四引擎：万相 3.0 / 海螺 H3 / Seedance / 可灵</b></div>
              {/* ★ 2026-10-06：团队模式**早就是五套** ✗ 这里还写着两套（过时 ✓） */}
              <div className="kv"><span>团队协作</span><b>员工卡 + 群聊 + 五套模式（点名派 / 全员广播 / 组长拆解 / 接力 / 开会）</b></div>
              {/* ★ 2026-10-06：沙箱**确实有** ✓ 但**默认是关的** ✗ —— 原来写法像默认开着 ✓ 改成如实说 ✓ */}
              <div className="kv"><span>沙箱</span><b>可选（**默认关**）：开 Docker 一次性容器，断网 / 限内存 / 非 root</b></div>
              {/* ★ 2026-10-06：工具数 21 → **22** ✓（README 那边有守卫 ✓，这里之前漏了 ✓ 一起盯上 ✓）*/}
              <div className="kv"><span>工具 / 提供者</span><b>22 个 / 10 个</b></div>
            </div>
            <div className="card">
              <div className="card-title">安全设计</div>
              <div className="kv"><span>数据</span><b>全存本机，不上传任何服务器</b></div>
              <div className="kv"><span>密钥</span><b>只存环境变量名，永不写入代码或配置文件</b></div>
              <div className="kv"><span>执行</span><b>危险命令弹审批（可选沙箱完全隔离）</b></div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
