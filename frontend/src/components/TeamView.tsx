import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { api, API_BASE, authedUrl } from '../api';
import { Users, Send, UserPlus, MessageSquare, X, Pencil, Trash2, Mic, Square } from 'lucide-react';
import { useVoiceDraft } from '../lib/useVoiceDraft';
import { ArtifactPanel } from './ArtifactPanel';
import { GroupProgress } from './GroupProgress';

type Employee = { id: string; name: string; dept: string; role: string; persona: string; mode?: string };
type Group = { id: string; name: string; members: string[]; leader?: string | null; mode?: string; relay_pos?: number; relay_total?: number; meeting_round?: number; meeting_rounds?: number; meeting_state?: string; meeting_summary?: string };
// §6.7：后端回写了 status/task_id/attachments —— 类型补齐，交付文件在群里可见
type FeedMsg = { seq: number; from: string; text: string; ts: string; status?: string; task_id?: string; attachments?: string[];
  /** ★ 群里的审批卡片：后端把"等待审批"播报进群时带上这个（call_id 用来回批） */
  approval?: { call_id: string; tool?: string; detail?: string } };

/** ★★ A-1 收尾（2026-10-07）：后端权威接口那一份待批 ✓
 *  `GET /team/groups/{gid}/approvals` ⇒ `{task_id, call_id, title, command}` ✓
 *  ★ 形状与 `FeedMsg` **不一样** ✗（这里没有 seq/from/text ✓ 也**不该有** ✓）——
 *    ⇒ 展示前拼成 `FeedMsg` 的形状 ✓（批还是走同一个 `doApprove` ✓ 不另写一条提交路径 ✗）。 */
type PendRow = { task_id: string; call_id: string; title: string; command: string };

// §6.6：部门/职位从后端 roster 拉（此前硬编码 12 个职位，后端实际 24 个）
const FALLBACK_DEPTS = ['调研部', '技术部', '策划部', '财务部', '运营部', '综合部'];
const MODE_LABEL: Record<string, string> = { manual: '点名派发', broadcast: '全员广播', leader: '组长拆解', relay: '接力', meeting: '开会' };

/** ★ 2026-10-06：长交付**默认折叠**（群里别再刷字墙 ✗）。
 *
 *  实测：一条交付动辄 2000+ 字（"改动文件/自测命令/用例清单/运行结果"小节齐全），
 *  几个人一交，群里就全是长文，真正要紧的（谁交了、过没过验收）反而被淹没。
 *  这里只折叠**显示**：前几行 + 「展开全文」，内容一字不改 ✓（长文另有去处：任务页）。
 */
function CollapsibleDelivery({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const t = String(text ?? '');
  const isLong = t.length > 320 || t.split('\n').length > 8;
  if (!isLong) return <div>{t}</div>;
  const head = t.split('\n').slice(0, 4).join('\n');
  return (
    <div>
      <div style={{ whiteSpace: 'pre-wrap' }}>{open ? t : head}</div>
      {!open && <div style={{ opacity: 0.5, marginTop: 2 }}>…</div>}
      <button className="btn-mini" style={{ marginTop: 4 }} onClick={() => setOpen(!open)}>
        {open ? '收起' : `展开全文（${t.length} 字）`}
      </button>
    </div>
  );
}


/** ★ 2026-10-06：群里"交付/验收"一眼能看懂的卡片。
 *
 *  实测痛点：群里消息两种极端 —— 要么是**一大段交付**（2000+ 字，看不出重点 ✗），
 *  要么是**一句验收结论**（✅/❌ 后面跟一长串理由 ✓ 但和普通聊天混在一起，扫不出来 ✗）。
 *  这里做两件小事（只改**显示**，不动内容 ✓）：
 *   · **验收结论**（以 ✅/❌/🟡/⛔ 开头的系统消息）⇒ 渲染成醒目的结论条（绿/红/黄）
 *   · **交付消息**（有多节标题）⇒ 顶部列出小节名，正文折叠（配合 CollapsibleDelivery）
 */
/** 这条消息是不是"结论"（✅/❌/⛔/🟡/⏳ 开头）—— 结论走结论条，就不再走普通正文 ✗。 */
function isVerdict(text: string): boolean {
  return ['✅', '❌', '⛔', '🟡', '⏳'].some((k) => String(text ?? '').trimStart().startsWith(k));
}

function VerdictCard({ text }: { text: string }) {
  const t = String(text ?? '');
  const first = t.trimStart().slice(0, 2);
  const kind = first.startsWith('✅') ? 'ok'
    : first.startsWith('❌') || first.startsWith('⛔') ? 'bad'
      : first.startsWith('🟡') || first.startsWith('⏳') ? 'warn' : '';
  if (!kind) return null;
  const color = kind === 'ok' ? 'rgba(63,196,138,0.16)' : kind === 'bad' ? 'rgba(240,110,110,0.16)' : 'rgba(240,196,90,0.16)';
  const line = t.split('\n')[0];
  const rest = t.split('\n').slice(1).join('\n');
  return (
    <div style={{ background: color, borderRadius: 8, padding: '6px 9px', marginTop: 2 }}>
      <div style={{ fontWeight: 600 }}>{line}</div>
      {rest && <div style={{ opacity: 0.85, marginTop: 3, whiteSpace: 'pre-wrap' }}>{rest}</div>}
    </div>
  );
}

const SECTION_KEYS = ['文件清单', '接口定义', '数据结构', '改动文件', '新增文件', '自测命令',
  '用例清单', '运行结果', '验收记录', '改动/新增文件'];

/** 从交付正文里挑出小节名（只用于**显示**一节导航条，内容不动）。 */
function sectionNames(text: string): string[] {
  const out: string[] = [];
  for (const raw of String(text ?? '').split('\n')) {
    const line = raw.trim().replace(/^[#*>\-·▸\s]+/, '');
    for (const k of SECTION_KEYS) {
      if (line.startsWith(k) && !out.includes(k) && line.length < 40) out.push(k);
    }
  }
  return out.slice(0, 6);
}


/** 从交付正文里挑出「怎么打开 / 怎么用」那一节的内容（只用于显示一行提示）。 */
function howToOpen(text: string): string {
  const lines = String(text ?? '').split('\n');
  for (let i = 0; i < lines.length; i++) {
    const bare = lines[i].replace(/^[#*>\-·▸\s]+/, '').trim();
    if (/怎么打开|怎么用|如何打开|打开方式|使用方法/.test(bare) && bare.length < 60) {
      const body = lines.slice(i + 1, i + 4).map((x) => x.trim()).filter(Boolean).join(' ');
      return (bare + (body ? '：' + body : '')).slice(0, 220);
    }
  }
  return '';
}


export function TeamView({ onClose, rootId }: { onClose(): void; rootId?: string }) {
  const [tab, setTab] = useState<'members' | 'chat'>('members');
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [gid, setGid] = useState<string | null>(null);
  const [feed, setFeed] = useState<FeedMsg[]>([]);
  // ★★ A-1 收尾（2026-10-07）：**后端权威的那份待批** ✓
  //   `null` = 还没拉到 / 拉失败 ⇒ 汇总**退回"这一屏 feed 里没批的"那份** ✓
  //   （宁可少数几条 ✓ 也绝不许因为接口挂了就白屏 / 报错 ✗）
  const [pendApi, setPendApi] = useState<PendRow[] | null>(null);
  const [input, setInput] = useState('');
  const feedRef = useRef<HTMLDivElement | null>(null);

  // ---- 员工卡表单（新建 / 编辑共用） ----
  const [fName, setFName] = useState('');
  const [fDept, setFDept] = useState('调研部');
  const [fRole, setFRole] = useState('');
  // ★ 2026-10-07：一键补齐角色卡（补几个由界面自己算 ✓）
  const [importing, setImporting] = useState(false);
  const [note, setNote] = useState('');
  const [fPersona, setFPersona] = useState('');
  const [fMode, setFMode] = useState<'expert' | 'free'>('expert');
  const [fErr, setFErr] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null); // null=新建
  const [roster, setRoster] = useState<{ role: string; dept: string; summary: string }[]>([]);
  const [maxMembers, setMaxMembers] = useState(24);

  // ---- 建群表单（此前 api.teamCreateGroup 零调用：根本没有建群 UI） ----
  const [gOpen, setGOpen] = useState(false);
  const [gName, setGName] = useState('');
  const [gMembers, setGMembers] = useState<string[]>([]);
  const [gMode, setGMode] = useState<'manual' | 'broadcast' | 'leader' | 'relay'>('manual');
  const [gLeader, setGLeader] = useState<string>('');
  const [gErr, setGErr] = useState('');
  const [gModeErr, setGModeErr] = useState('');   // ★ 7c：改模式失败时的提示（如"切组长模式但没组长"）
  // ★ 群内审批（2026-10-05）：哪个 call_id 已批过 + 正在批哪个（防手滑重复点）
  const [handled, setHandled] = useState<Record<string, string>>({});
  const [apBusy, setApBusy] = useState('');

  // ---- @ 成员提示 ----
  const [mentionAt, setMentionAt] = useState<number | null>(null); // input 中 @ 的位置
  const [mentionIdx, setMentionIdx] = useState(0);
  const [pickOpen, setPickOpen] = useState(false);   // 成员一键选择面板（不用先知道名字）
  // ★ 语音输入（2026-10-05）：与首页/任务内**共用同一套实现**（lib/useVoiceDraft）——
  //   团队输入框此前没有语音。按住右 Ctrl 也能说（快捷键在 hook 里统一处理）。
  const voice = useVoiceDraft({ onText: (t) => setInput((prev) => (prev ? prev + ' ' + t : t)) });

  const loadAll = (): void => {
    void api.teamEmployees().then((d) => setEmployees(d.employees ?? []));
    void api.teamGroups().then((d) => setGroups(d.groups ?? []));
    void api.teamRoles().then((d) => {
      setRoster(d.roles ?? []);
      setMaxMembers(d.max ?? 24);
      // 默认选中第一个角色（后端顺序即推荐顺序）
      if (d.roles?.length) {
        setFRole((prev) => prev || d.roles[0].role);
        setFDept((prev) => prev || d.roles[0].dept);
      }
    }).catch(() => undefined);
  };
  useEffect(loadAll, []);

  /** ★★ A-1 收尾：拉一次**权威的**待批清单 ✓
   *  （进群时 ✓ 每次刷新 feed 时 ✓ 批完一次之后 ✓ —— 三处都调它 ✓）
   *  为什么不能只数 feed ✗：审批卡片可能落在**还没加载到的更早消息**里 ✓
   *  那一屏就数不到它 ⇒ 用户看到的"还差 N 个"**偏小** ✓（这正是本项要收的尾 ✓）。
   *  拉失败 ⇒ 置 `null` ⇒ 汇总沿用 feed 那份 ✓（界面不许崩 ✗ 也不许假装 0 条 ✓）。
   *  ★ 只读接口 ✓ 不改任何状态 ✓ 也不会像"另写一条提交路径"那样和 `doApprove` 打架 ✓ */
  const loadPend = (): void => {
    const g = gid;
    if (!g) return;
    void api.groupApprovals(g)
      .then((r) => setPendApi(r?.approvals ?? []))
      .catch(() => setPendApi(null));
  };
  // 打磨（§5.11）：全屏层支持 Escape 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // 二十一轮 🔴2：打开时把主容器 #root 设 inert（背景不可聚焦/不可交互），
  // 焦点移到 overlay 内首个可聚焦元素；关闭/卸载时恢复。
  const overlayRef = useRef<HTMLDivElement | null>(null);
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

  useEffect(() => {
    if (!gid) return;
    let alive = true; let seq = 0;
    const tick = (): void => {
      void api.teamFeed(gid, seq).then((d) => { if (!alive) return; const ms = d.messages ?? []; if (ms.length) { seq = ms[ms.length - 1].seq; setFeed((p) => [...p, ...ms]); } }).catch(() => undefined);
      // ★ A-1 收尾：与 feed **同频**刷新那份权威待批 ✓ ——
      //   别的窗口批了 / 在任务页批了 ⇒ 群里那个数也跟着减 ✓（不然它就是个"过期数"✓）
      loadPend();
    };
    setPendApi(null);          // 换群 ⇒ 先把上一份清掉 ✓（不许把 A 群的待批显示在 B 群 ✗）
    tick(); const t = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(t); };
  }, [gid]);

  useEffect(() => { if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight; }, [feed.length]);
  const say = (): void => { const v = input.trim(); if (!v || !gid) return; setInput(''); setMentionAt(null); void api.teamSay(gid, v).then(() => { void api.teamFeed(gid, 0).then((d) => { setFeed(d.messages ?? []); }); }); };

  const resetForm = (): void => {
    setEditingId(null); setFName(''); setFPersona(''); setFErr('');
    setFMode('expert');
    if (roster.length) { setFRole(roster[0].role); setFDept(roster[0].dept); }
  };
  const startEdit = (e: Employee): void => {
    setEditingId(e.id); setFName(e.name); setFDept(e.dept); setFRole(e.role);
    setFPersona(e.persona || ''); setFMode(e.mode === 'free' ? 'free' : 'expert'); setFErr('');
  };
  const submitEmployee = (): void => {
    setFErr('');
    const card = { name: fName.trim(), dept: fDept, role: fRole, mode: fMode, persona: fMode === 'free' ? fPersona : '' };
    const req = editingId
      ? api.teamUpdateEmployee(editingId, card)
      : api.teamAddEmployee(card);
    void req.then(() => { resetForm(); loadAll(); })
      .catch((e) => setFErr(e instanceof Error ? e.message : String(e)));
  };
  const delEmployee = (e: Employee): void => {
    if (!confirm(`删除员工「${e.name}」？该员工也会从所有群中移除，其历史任务不受影响。`)) return;
    void api.teamDelEmployee(e.id).then(() => { if (editingId === e.id) resetForm(); loadAll(); })
      .catch((err) => setFErr(err instanceof Error ? err.message : String(err)));
  };

  const createGroup = (): void => {
    setGErr('');
    if (!gName.trim()) { setGErr('先给群起个名字'); return; }
    if (!gMembers.length) { setGErr('至少勾选一名成员'); return; }
    if (gMode === 'leader' && !gLeader) { setGErr('组长模式需要选一名组长'); return; }
    void api.teamCreateGroup(gName.trim(), gMembers, gMode === 'leader' ? gLeader : undefined, gMode)
      .then(() => { setGOpen(false); setGName(''); setGMembers([]); setGLeader(''); setGMode('manual'); loadAll(); })
      .catch((e) => setGErr(e instanceof Error ? e.message : String(e)));
  };
  const delGroup = (g: Group): void => {
    if (!confirm(`删除群「${g.name}」？聊天记录将一并删除（员工卡不受影响）。`)) return;
    void api.teamDeleteGroup(g.id).then(() => { if (gid === g.id) { setGid(null); setFeed([]); } loadAll(); });
  };

  // ★ 群内审批（2026-10-05 用户要求）：他们的活、要点的允许，都该在群里就地批。
  //   handled 记录"哪个 call_id 已经批过"，避免手滑重复点（后端也会按 call_id 拒重复）。
  const doApprove = (m: FeedMsg, decision: 'once' | 'always' | 'all' | 'forever' | 'deny'): void => {
    const cid = m.approval?.call_id;
    if (!cid || !m.task_id) return;
    setApBusy(cid);
    setGModeErr('');
    // ★ 2026-10-06：把命令原文带上 —— 选「这类以后都别问」时，后端要用它算"这类"是哪个程序 ✓
    void api.teamApprove(gid ?? '', m.task_id, cid, decision, m.approval?.detail ?? '')
      .then(() => {
        const word = decision === 'once' ? '允许一次'
          : decision === 'always' ? '本任务都允许'
            : decision === 'all' ? '本任务全部允许（含新命令）'
              : decision === 'forever' ? '这类以后都别问' : '拒绝';
        setHandled((h) => ({ ...h, [cid]: word }));
        loadAll();
        // ★ A-1 收尾：批完立刻重拉那份权威清单 ✓（数当场就减 ✓ 不用等下一个 tick ✓）
        loadPend();
      })
      .catch((e) => setGModeErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setApBusy(''));
  };

  // @ 提示：检测输入中最后一个未闭合的 @
  const onInputChange = (v: string): void => {
    setInput(v);
    const at = v.lastIndexOf('@');
    if (at >= 0 && !/\s/.test(v.slice(at + 1))) { setMentionAt(at); setMentionIdx(0); }
    else setMentionAt(null);
  };
  const insertMention = (name: string): void => {
    if (mentionAt == null) return;
    const v = input.slice(0, mentionAt) + `@${name} ` + input.slice(mentionAt + 1).replace(/^[^\s]*/, '');
    onInputChange(v);
    setMentionAt(null);
  };
  const mentionCandidates = mentionAt == null ? [] : employees.filter((e) =>
    `@${e.name}`.toLowerCase().startsWith(input.slice(mentionAt).toLowerCase()));

  const curGroup = groups.find((g) => g.id === gid);

  /** ★ 2026-10-06：这张员工卡**在为哪些群干活** ✓ ——
   *  用户实测提问"建一个群聊就得新建一个员工卡呀？" ✗ 说明界面上完全看不出
   *  卡是**可复用**的 ✓（同一个人可以同时待在好几个群里 ✓ 人设技能都一样 ✓）。 */
  const groupsOf = (empId: string): string[] =>
    groups.filter((g) => (g.members ?? []).includes(empId)).map((g) => g.name);

  /** ★ 2026-10-06（用户提的）：**在群里就地预览产物** ✓ ——
   *  点产物不再跳新窗口、也不用跑去任务页 ✓ 直接在群聊上弹一层看 ✓。
   *  预览本身**复用任务页那个面板**（`ArtifactPanel` ✓ 图/网页/文本/表格/视频它都认 ✓）
   *  ⇒ 不重写预览逻辑 ✓ 也就不会出现"两处行为不一致" ✓。 */
  const [artTask, setArtTask] = useState<string | null>(null);
  /** 点的是哪个产物 ⇒ 弹层里**直接展开它** ✓（省掉"再找一遍再点一次"那一步 ✓）*/
  const [artFile, setArtFile] = useState<string>('');  const nameOf = (id: string): string => employees.find((e) => e.id === id)?.name || id;

  const inputStyle: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '7px 9px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.28)', background: 'rgba(255,255,255,0.07)', color: 'inherit', fontSize: 13 };
  const cardBtn: React.CSSProperties = { border: 'none', background: 'transparent', color: 'inherit', cursor: 'pointer', padding: 4, borderRadius: 6, opacity: 0.55 };

  // ★★ 2026-10-07（第 10 项 ③）：**员工头像** ✓ —— 首字 + 按名字定色的圆 ✓
  //   为什么不搞"上传图片"✗：那要加接口 ✓ 加存储 ✓ 加裁剪 ✓ 加失败处理 ✓
  //     而这一步要解决的是**"一列人名分不出谁是谁"**✓ ——
  //     首字 + 稳定的颜色就够用 ✓ 而且**同一张卡在任何地方颜色都一样** ✓（按名字算 ✓ 不随机 ✗）
  //   ★ 副作用是好的：**不用联网、不用存文件、零后端改动** ✓
  const avatarOf = (name: string): { ch: string; color: string } => {
    const s = (name || '').trim() || '?';
    let h = 0;
    for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360;   // 同名字 ⇒ 同颜色 ✓（不抖 ✗）
    // ★ 中文名取第一个字 ✓ 英文名取首字母大写 ✓（都用 charAt ✓ 别用 slice 切坏代理对 ✓）
    return { ch: s.charAt(0).toUpperCase(), color: `hsl(${h} 58% 40%)` };
  };
  const Avatar = ({ name, size = 22 }: { name: string; size?: number }) => {
    const a = avatarOf(name);
    return (
      <span title={name} style={{
        width: size, height: size, borderRadius: '50%', background: a.color, color: '#fff',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        fontSize: Math.round(size * 0.52), fontWeight: 600, flex: '0 0 auto', userSelect: 'none',
      }}>{a.ch}</span>
    );
  };

  // 二十六轮第 4 批第 1 处：focus trap 闭环——与设置页同源机制（inert 只护
  // #root，Tab 越过末控件掉 body：实测 60 Tab 第 30 次逃逸、Shift+Tab 第 1 次）。
  // Tab 到末尾回卷首个、Shift+Tab 到首个回卷末尾；关闭归还焦点由 inert
  // cleanup 的 prevFocus 承担。
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
    <div ref={overlayRef} role="dialog" aria-modal="true" aria-label="团队管理" onKeyDown={trapTab}
         style={{ position: 'fixed', inset: 0, zIndex: 200, display: 'flex', background: 'var(--bg)', color: 'inherit' }}>  {/* 十八轮🟠5②：> sidebar-toggle z70，返回按钮不再被 ☰ 盖住 */}
        {/* 二十五轮 🟠5②：窄档图标化（font-size:0）后按钮只剩图标——补 title
            供悬停/读屏识别（宽档 title 无副作用） */}
        <div className="team-nav" style={{ width: 170, flexShrink: 0, borderRight: '1px solid rgba(255,255,255,0.08)', padding: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <button className="settings-back" aria-label="返回工作区" title="返回工作区" onClick={onClose}>← 返回工作区</button>
          <button className={`nav-item ${tab === 'members' ? 'nav-on' : ''}`} aria-label="员工卡" title="员工卡" onClick={() => setTab('members')}><Users size={14} style={{ verticalAlign: -2, marginRight: 6 }} />员工卡</button>
          <button className={`nav-item ${tab === 'chat' ? 'nav-on' : ''}`} aria-label="群聊" title="群聊" onClick={() => setTab('chat')}><MessageSquare size={14} style={{ verticalAlign: -2, marginRight: 6 }} />群聊</button>
        </div>
      <div className="team-content" style={{ flex: 1, minWidth: 0, padding: '14px 10px', overflowY: 'auto' }}>  {/* 二十六轮第3批第4处：横向 padding 全档统一 10px——此前 ≤600 档 10px、>600 档 18px 的断点使 .team-chat-grid 在 600→601 跳 15px，群列表 42% 随之出现 −6.29px 台阶 */}
        {tab === 'members' && (
          <>
            <h2 style={{ margin: '0 0 12px', display: 'flex', alignItems: 'center', gap: 8 }}><Users size={18} /> 员工卡 <span style={{ fontSize: 12, opacity: 0.5 }}>（{employees.length}/{maxMembers}）</span></h2>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(180px, 100%), 1fr))', gap: 10, marginBottom: 16 }}>  {/* 十八轮：min() 下限——极窄容器不撑破 */}
              {employees.map((e) => (
                <div key={e.id} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 12px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    {/* ★ 第 10 项 ③：员工头像 ✓（首字 + 定色 ✓ 见 avatarOf 那段说明 ✓） */}
                    <Avatar name={e.name} />
                    <b style={{ flex: 1, minWidth: '3.2em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={e.name}>{e.name}</b>
                    <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 8, background: e.mode === 'free' ? 'rgba(63,185,80,0.18)' : 'rgba(107,163,245,0.15)' }}>{e.mode === 'free' ? '自由人设' : '专家'}</span>
                    <button style={cardBtn} title="编辑" onClick={() => startEdit(e)}><Pencil size={13} /></button>
                    <button style={{ ...cardBtn, color: '#f0736a' }} title="删除" onClick={() => delEmployee(e)}><Trash2 size={13} /></button>
                  </div>
                  <div style={{ fontSize: 11, opacity: 0.6 }}>{e.dept} · {e.role}</div>
                  <div style={{ fontSize: 12, opacity: 0.65, marginTop: 4 }}>
                    {e.mode === 'free' ? (e.persona || '（未填人设）') : `按「${e.role}」专家人设执行`}
                  </div>
                  {/* ★ 2026-10-06（用户问："建群就得新建员工卡呀？" ✗）：把"这张卡在为哪些群干活"
                      直接印在卡上 ✓ —— 一眼看出**卡是可复用的** ✓（原来完全看不出来 ✓） */}
                  <div style={{ fontSize: 11, opacity: 0.5, marginTop: 4 }}>
                    {groupsOf(e.id).length
                      ? `在 ${groupsOf(e.id).length} 个群里：${groupsOf(e.id).slice(0, 3).join('、')}${groupsOf(e.id).length > 3 ? ' …' : ''}`
                      : '还没进过群 —— 去「群聊」点「＋ 新建群」勾上它就行 ✓'}
                  </div>
                </div>
              ))}
              {employees.length === 0 && <div style={{ gridColumn: '1 / -1', opacity: 0.4, fontSize: 13, padding: 12 }}>还没有员工卡——用下面的表单创建第一张，再回群聊建群。</div>}
            </div>
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 12px', maxWidth: 480 }}>
              <div style={{ fontWeight: 600, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                {editingId ? <><Pencil size={14} /> 编辑员工卡</> : <><UserPlus size={14} /> 新建员工卡</>}
                {editingId && <button style={{ ...cardBtn, marginLeft: 'auto' }} onClick={resetForm}>取消编辑</button>}
              </div>
              <label style={{ display: 'block', marginBottom: 8 }}><span style={{ fontSize: 12, opacity: 0.7 }}>名字</span><input style={inputStyle} value={fName} onChange={(e) => setFName(e.target.value)} placeholder="如：小米" /></label>
              <label style={{ display: 'block', marginBottom: 8 }}><span style={{ fontSize: 12, opacity: 0.7 }}>职位（来自后端 {roster.length} 个专家角色）</span>
                <select style={inputStyle} value={fRole} onChange={(e) => {
                  setFRole(e.target.value);
                  const r = roster.find((x) => x.role === e.target.value);
                  if (r) setFDept(r.dept); // 部门跟随职位
                }}>
                  {roster.map((r) => <option key={r.role} value={r.role}>{r.role}（{r.dept}）</option>)}
                  {!roster.length && FALLBACK_DEPTS.map((d) => <option key={d}>{d}</option>)}
                </select>
              </label>
              {/* ★ 2026-10-07（用户提的"12 个没用上的角色要不要加满"✓）：
                  角色库有 24 个 ✓ 他的卡只建了 12 个 ✗ ⇒ 想用剩下的得一个个手建 ✓ 太麻烦 ✓
                  ⇒ 给一个**一键补齐** ✓ 点了才建 ✓（不擅自往你数据里塞 ✗）
                  接口只补缺的 ✓ 已有卡一个不动 ✓ 而且**只建卡不建群** ✓（群你自己拉 ✓） */}
              {(() => {
                const have = new Set(employees.map((e) => e.name));
                const miss = roster.filter((r) => !have.has(r.role)).length;
                if (!miss) return null;
                return (
                  <div style={{ marginBottom: 8 }}>
                    <button style={cardBtn} disabled={importing} onClick={async () => {
                      setImporting(true);
                      try {
                        const r = await api.importRoleCards();
                        setNote(r.note || '已补齐');
                        loadAll();
                      } catch (e) {
                        setNote(e instanceof Error ? e.message : String(e));
                      } finally {
                        setImporting(false);
                      }
                    }}>
                      {importing ? '补齐中…' : `一键补齐剩下 ${miss} 个角色`}
                    </button>
                    <span style={{ fontSize: 11, opacity: 0.55, marginLeft: 8 }}>
                      只建卡 ✓ 不建群 ✓ 已有的卡一个不动 ✓
                    </span>
                    {/* 点了之后**把结果说出来** ✓（补了几个 ✓ 跳过了几个 ✓ 不装作没发生 ✓） */}
                    {!!note && <div style={{ fontSize: 11, opacity: 0.75, marginTop: 4 }}>{note}</div>}
                  </div>
                );
              })()}
              <label style={{ display: 'block', marginBottom: 8 }}><span style={{ fontSize: 12, opacity: 0.7 }}>模式</span>
                <select style={inputStyle} value={fMode} onChange={(e) => setFMode(e.target.value === 'free' ? 'free' : 'expert')}>
                  <option value="expert">专家模式：按角色库的标准人设执行（更稳）</option>
                  <option value="free">自由人设：用我自己写的人设（下方生效）</option>
                </select>
              </label>
              <label style={{ display: 'block', marginBottom: 8, opacity: fMode === 'free' ? 1 : 0.4 }}><span style={{ fontSize: 12, opacity: 0.7 }}>人设{fMode === 'expert' ? '（专家模式下忽略）' : ''}</span><textarea style={{ ...inputStyle, minHeight: 54 }} value={fPersona} disabled={fMode !== 'free'} onChange={(e) => setFPersona(e.target.value)} placeholder="例：你是客服小芳，语气亲切…" /></label>
              <button className="btn-save" disabled={!fName.trim()} onClick={submitEmployee}>{editingId ? (fName.trim() ? '保存修改' : '填名字') : (fName.trim() ? '创建员工卡' : '填名字')}</button>
              {fErr && <div style={{ fontSize: 12, color: '#f0736a', marginTop: 6 }}>{fErr}</div>}
            </div>
          </>
        )}
        {tab === 'chat' && (
          // ★★ A-3（2026-10-07 深夜）：**手机档收成一列** —— `has-group` 由「选了群没」驱动 ✓
          //   390px 实量（真机宽度）：群列表 131px + 聊天列只剩 **192px** ✗
          //     ⇒ 气泡最宽 115px、一句话折成 4~5 行、群名只剩 3~5 个字 ✓
          //   ⇒ ≤760px：**选中的群**把列表收起来，聊天占满（标准"主从"形态 ✓
          //     点聊天头上那个「← 群列表」就回去 ✓ 没选群时列表占满 ✓）
          //   ★ 边角（我这一行自己带出来的 ✓ 自己收拾 ✓）：`has-group` 必须**同时**看
          //     `gid` 与 `curGroup` ✗ —— 只看 `gid` 的话，"群在别处被删了"（局域网手机直连时
          //     真会发生 ✓）就会出现"列表收起了 + 中间一句『选一个群』" = **窄档死路** ✗
          <div className={`team-chat-grid${gid && curGroup ? ' has-group' : ''}`} style={{ display: 'flex', height: 'calc(100vh - 110px)', overflow: 'hidden', borderRadius: 10, border: '1px solid rgba(255,255,255,0.08)' }}>
            {/* 二十五轮 🔴4-1：群列表列宽改为类驱动（.team-grouplist）——
                ≤600 档 38% 曾成主约束（320 视口 = 121px），群名换行竖排；
                根因修复 = 群名单行 ellipsis（.team-group-name）+ 窄档列宽 42% */}
            <div className="team-grouplist" style={{ flexShrink: 0, borderRight: '1px solid rgba(255,255,255,0.08)', overflowY: 'auto', padding: 8, display: 'flex', flexDirection: 'column' }}>
              <button className="new-chat-btn" onClick={() => { setGOpen((v) => !v); setGErr(''); }} style={{ marginBottom: 8 }}>＋ 新建群</button>
              {gOpen && (
                <div style={{ border: '1px solid rgba(107,163,245,0.35)', borderRadius: 10, padding: 10, marginBottom: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <input style={inputStyle} value={gName} onChange={(e) => setGName(e.target.value)} placeholder="群名，如：新品推广项目组" />
                  <div>
                    {/* ★ 2026-10-06（用户实测提问："建一个群聊就得新建一个员工卡呀？" ✗）：
                        产品不是那样 ✓ —— 但界面上没说清 ✓ 这里一句话讲明白 ✓ */}
                    <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>
                      勾选成员（{gMembers.length} 人）
                      <span style={{ marginLeft: 6, color: '#7fb3ff' }}>
                        —— 勾**已有**的员工卡就行 ✓ <b>不用新建</b> ✓
                      </span>
                    </div>
                    <div style={{ fontSize: 11, opacity: 0.45, marginBottom: 6 }}>
                      同一个人可以同时待在好几个群里 ✓（人设、技能都一样 ✓）——
                      只有"要一个新角色"时才需要新建卡 ✓
                    </div>
                    {employees.map((e) => (
                      // ★ 2026-10-06：这一行原来把"已在 N 个群…"塞在名字后面 ⇒
                      //   在窄栏里**把名字挤成竖排**（截图里"调 研 员"一列一个字 ✗）
                      //   ⇒ 改成**两行**：第一行名字+角色 ✓ 第二行小字"在哪些群" ✓
                      <label key={e.id} style={{ display: 'block', fontSize: 12.5, padding: '3px 2px', cursor: 'pointer' }}>
                        <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                          <input type="checkbox" checked={gMembers.includes(e.id)} onChange={(ev) => {
                            setGMembers((p) => ev.target.checked ? [...p, e.id] : p.filter((x) => x !== e.id));
                          }} />
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.name}</span>
                          <span style={{ opacity: 0.45, fontSize: 11, flexShrink: 0 }}>{e.role}</span>
                        </span>
                        {/* ★ 让他一眼看出"这张卡已经在别的群里干活" ✓（卡是可复用的 ✓） */}
                        {!!groupsOf(e.id).length && (
                          <span style={{ display: 'block', marginLeft: 22, opacity: 0.4, fontSize: 10.5,
                                         overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            已在 {groupsOf(e.id).slice(0, 2).join('、')}
                            {groupsOf(e.id).length > 2 ? ` 等 ${groupsOf(e.id).length} 个群` : ''}
                          </span>
                        )}
                      </label>
                    ))}
                    {!employees.length && <div style={{ fontSize: 11, opacity: 0.4 }}>还没有员工，先去「员工卡」创建</div>}
                  </div>
                  <label style={{ fontSize: 12, opacity: 0.7 }}>派发模式
                    <select style={inputStyle} value={gMode} onChange={(e) => setGMode(e.target.value as 'manual' | 'broadcast' | 'leader' | 'relay')}>
                      <option value="manual">点名派发：@谁谁干（默认）</option>
                      <option value="broadcast">全员广播：每人认领执行</option>
                      <option value="leader">组长拆解：你给目标，组长分派（并行）</option>
                      <option value="relay">接力：一个做完交给下一个（串行）</option>
                      <option value="meeting">开会：围绕议题讨论 → 出会议纪要</option>
                    </select>
                  </label>
                  {gMode === 'leader' && (
                    <label style={{ fontSize: 12, opacity: 0.7 }}>组长
                      <select style={inputStyle} value={gLeader} onChange={(e) => setGLeader(e.target.value)}>
                        <option value="">选一名组长…</option>
                        {gMembers.map((id) => <option key={id} value={id}>{nameOf(id)}</option>)}
                      </select>
                    </label>
                  )}
                  <button className="btn-save" onClick={createGroup}>创建群</button>
                  {gErr && <div style={{ fontSize: 12, color: '#f0736a' }}>{gErr}</div>}
                </div>
              )}
              <div style={{ fontSize: 11, opacity: 0.5, padding: '4px 6px' }}>群列表</div>
              {groups.map((g) => (
                <div key={g.id} style={{ display: 'flex', alignItems: 'center', marginBottom: 2 }}>
                  <button style={{ flex: 1, minWidth: 0, textAlign: 'left', padding: '9px 10px', borderRadius: 8, border: 'none', background: gid === g.id ? 'rgba(107,163,245,0.15)' : 'transparent', color: 'inherit', cursor: 'pointer', fontSize: 13, overflow: 'hidden' }} onClick={() => { setGid(g.id); setFeed([]); }}>
                    <b className="team-group-name" title={g.name}>{g.name}</b>
                    <div style={{ fontSize: 10.5, opacity: 0.45 }}>
                      {MODE_LABEL[g.mode || 'manual']}{g.mode === 'leader' && g.leader ? ` · ${nameOf(g.leader)}` : ''} · {g.members.length}人
                      {/* 接力进行中：显示"第几棒"——用户一眼看出这条链跑到哪了 */}
                      {g.mode === 'relay' && (g.relay_pos ?? 0) > 0 && (
                        <span style={{ marginLeft: 6, color: '#6ba3f5' }}>第 {g.relay_pos}/{g.relay_total} 棒</span>
                      )}
                      {/* 开会进行中：显示"第几轮"，结束后显示"已出纪要" */}
                      {g.mode === 'meeting' && (g.meeting_state === 'running'
                        ? <span style={{ marginLeft: 6, color: '#6ba3f5' }}>讨论中 第 {g.meeting_round ?? 0}/{g.meeting_rounds ?? 0} 轮</span>
                        : g.meeting_summary
                          ? <span style={{ marginLeft: 6, color: '#7fc08a' }}>已出纪要</span>
                          : null)}
                    </div>
                  </button>
                  <button style={{ ...cardBtn, color: '#f0736a' }} title="删除群" onClick={() => delGroup(g)}><X size={13} /></button>
                </div>
              ))}
              {groups.length === 0 && !gOpen && <div style={{ fontSize: 11, opacity: 0.4, padding: '6px' }}>还没有群——点上面「新建群」</div>}
            </div>
            <div className="team-chat-col" style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
              {gid && curGroup ? (
                <>
                  <div style={{ padding: '10px 14px', borderBottom: '1px solid rgba(255,255,255,0.08)', fontSize: 14, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    {/* ★★ A-3：窄档回群列表（宽档不显示 ✓ 见 styles.css `.team-back`）——
                        手机上一屏只放得下一列 ✓ 没有它就"回不去列表"了 ✗ */}
                    <button className="team-back" title="回到群列表" aria-label="回到群列表"
                      onClick={() => { setGid(null); setFeed([]); }}>← 群列表</button>
                    {curGroup.name}
                    {/* ★ 二十六轮第 7 批第 7c 处：模式【建群后也能改】。
                        原来这里是个只读徽章 —— 想换模式只能删群重建（聊天记录一起没），
                        用户原话："这几个模式……创建群聊之后在群聊里都可以选择可不可以"。
                        改成下拉，改完即时生效；后端拒绝（如"切组长模式但没组长"）就就地显示原因。 */}
                    <select
                      value={curGroup.mode || 'manual'}
                      title="派发模式（建群后也能改）"
                      aria-label="派发模式"
                      onChange={async (e) => {
                        setGModeErr('');
                        try {
                          await api.teamUpdateGroup(curGroup.id, { mode: e.target.value });
                          loadAll();
                        } catch (err) {
                          setGModeErr(err instanceof Error ? err.message : String(err));
                        }
                      }}
                      style={{ fontSize: 11, fontWeight: 400, padding: '2px 6px', borderRadius: 8, border: '1px solid var(--border)', background: 'rgba(107,163,245,0.12)', color: 'inherit', cursor: 'pointer' }}
                    >
                      <option value="manual">{MODE_LABEL.manual}</option>
                      <option value="broadcast">{MODE_LABEL.broadcast}</option>
                      <option value="leader">{MODE_LABEL.leader}</option>
                      {/* ★ 群内改模式这个下拉也要有接力 —— 否则"新建时能选、建完改不了"
                          （本班实测：验证脚本抓到的正是这个下拉，它当时只有三项） */}
                      <option value="relay">{MODE_LABEL.relay}</option>
                      <option value="meeting">{MODE_LABEL.meeting}</option>
                    </select>
                    {curGroup.mode === 'leader' && curGroup.leader && <span style={{ fontSize: 11, fontWeight: 400, opacity: 0.5 }}>组长：{nameOf(curGroup.leader)}</span>}
                    {/* ★ 重新出纪要（2026-10-05）：收口那一步最容易撞上空响应，旧实现会让
                        整场会白开（讨论记录还在、就是没纪要）。这个按钮用已存记录再收一次口。 */}
                    {curGroup.mode === 'meeting' && (
                      <button
                        className="btn-mini"
                        title="用已有的讨论记录重新生成会议纪要（不会重跑讨论、不再花讨论的钱）"
                        onClick={async () => {
                          setGModeErr('');
                          try {
                            await api.resummarizeMeeting(curGroup.id);
                            void loadAll();
                          } catch (e) {
                            setGModeErr(e instanceof Error ? e.message : String(e));
                          }
                        }}
                      >
                        重新出纪要
                      </button>
                    )}
                    {gModeErr && <span style={{ fontSize: 11, fontWeight: 400, color: '#f0883e' }}>{gModeErr}</span>}
                  </div>
                  {/* ★ 2026-10-06（用户提的）：**「这活到哪一步了」** —— 常驻在消息流**上面** ✓
                      滚来滚去它一直在 ✓（原来的分工单只是**一条消息** ✓ 滚上去就找不着了 ✗）。
                      没有分工单（不是组长模式/还没拆解）时它自己返回 null ✓ 不留空框 ✓。 */}
                  {curGroup && <GroupProgress group={curGroup as never} />}
                  {/* ★★ 2026-10-07（第 10 项 ②）**多审批汇总** ✓ —— 原来审批卡片散在 feed 里**一条条刷屏** ✗
    用户得一个个点 ✓ 而且**看不出还有几条在等** ✗（点完一条才发现下面还有 ✓）
    ⇒ 在消息列表上方给一块汇总：「还差 N 个决定」+ 每人一行 + 点一下就地批 ✓
    ★★ A-1 收尾（2026-10-07）：主数改成**后端权威接口**那份 ✓
      （`pendApi` ⇐ `GET /team/groups/{gid}/approvals` ✓）
      为什么非换不可 ✗：原来只数"**这一屏 feed 里**还没批的" ✓ ⇒
        审批卡片若落在**还没加载到的更早消息**里，这一屏**数不到它** ⇒ N 偏小 ✓
      ★ 接口还没回来 / 挂了 ⇒ **退回这一屏 feed 那份** ✓（`pendApi === null` ✓ 界面不许崩 ✗）
      ★ 两份都过一遍 `handled` ✓（刚批过、后端还没反映过来的那一下也不会重复显示 ✓）
    ★ 批还是走原来那个 `doApprove` ✓ —— 不另写一条提交路径 ✗（否则两处语义迟早不一致 ✓
      比如"这类以后都别问"的确认文案、`apBusy` 防手滑、失败提示 ✓ 都会漏 ✓）
    ★ 接口给的是 `{task_id, call_id, title, command}` ✗ 不是 `FeedMsg` ✓
      ⇒ 这里拼成 `doApprove` 认的形状 ✓（它只读 `m.approval?.call_id` / `m.task_id` / `m.approval?.detail` ✓）
    ★★ A-3（2026-10-07 深夜）：**这一块从滚动区里搬出来了** ✓ —— 与 `GroupProgress` 同一层（常驻 ✓）
      为什么非搬不可 ✗：它原来在消息列表**最上面** ✓ 而 feed 一进来就**自动滚到底** ✗
        ⇒ 390px 实量：卡片顶边在可视区**上方 573px** ✓ 有历史记录的群**根本看不到它** ✗
        （块本身是对的 ✓ 位置错了 ✓ —— 用户压根看不见的提示等于没有 ✓）
      ★ 断言仍是"在消息列表**上方**"✓ 而且现在**滚不走了** ✓ 比原来更符合那条测试的本意 ✓ */}
{(() => {
  const fromFeed = feed.filter((m) => m.approval?.call_id && !handled[m.approval.call_id]);
  // ★ 接口那份拼成 `FeedMsg` 的形状 ✓（`doApprove` 只读 call_id / task_id / detail ✓ 够用 ✓）
  const asMsg = (r: PendRow): FeedMsg & { title?: string } =>
    ({ task_id: r.task_id, title: r.title,
       approval: { call_id: r.call_id, tool: '命令', detail: r.command } } as FeedMsg & { title?: string });
  const pend: (FeedMsg & { title?: string })[] = (pendApi === null
    ? fromFeed
    : pendApi.map(asMsg)
  ).filter((m) => !!m.approval?.call_id && !handled[m.approval.call_id]);
  if (!pend.length) return null;
  return (
    // ★ 横向 14px = 与消息区同一条对齐线 ✓（`GroupProgress` 也是 14px ✓ 两块摞着才齐 ✓）
    <div className="card" style={{ margin: '0 14px 8px', padding: '8px 10px', borderLeft: '3px solid #f0a05a', flexShrink: 0 }}>
      <b style={{ fontSize: 13 }}>⚠️ 还有 {pend.length} 个决定等你</b>
      <span style={{ fontSize: 11, opacity: 0.6, marginLeft: 8 }}>
        （全群口径 ✓ 含还没翻到的更早消息 ✓ 批完就没了 ✓）
      </span>
      {pend.map((m) => (
        <div key={m.approval!.call_id}
          style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, opacity: 0.85 }}>{m.title || m.approval?.tool || '动作'}</span>
          {/* ★ A-3：窄档靠 `.pend-cmd` 让它**独占一行并换行显示全** ✓
              （390px 实量：原来这条被压到 13~41px 宽 ✓ 而命令本身 265px ✓ 只剩两三个字 ✗） */}
          <span className="mono pend-cmd" style={{
            fontSize: 11, opacity: 0.6, flex: 1, minWidth: 0,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }} title={m.approval?.detail}>{m.approval?.detail}</span>
          <button style={cardBtn} disabled={apBusy === m.approval!.call_id}
            title="只允许这次" onClick={() => doApprove(m, 'once')}>批准</button>
          <button style={{ ...cardBtn, color: '#f0736a' }} disabled={apBusy === m.approval!.call_id}
            title="拒绝" onClick={() => doApprove(m, 'deny')}>拒绝</button>
        </div>
      ))}
    </div>
  );
})()}
                  <div ref={feedRef} style={{ flex: 1, overflowY: 'auto', padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
{feed.map((m) => {
                      const who = m.from === 'user' ? '我' : m.from.startsWith('emp:') ? m.from.slice(4) : '系统';
                      const mine = m.from === 'user';
                      const hhmm = m.ts ? new Date(m.ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '';
                      return (
                        <div key={m.seq} style={{ display: 'flex', justifyContent: mine ? 'flex-end' : 'flex-start' }}>
                          <div style={{ maxWidth: '75%', minWidth: 0, padding: '8px 12px', borderRadius: 10, fontSize: 13.5, background: mine ? 'rgba(107,163,245,0.18)' : 'rgba(255,255,255,0.06)', overflowWrap: 'anywhere' }}>
                            <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 2, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5 }}>
                              {/* ★ 第 10 项 ③：发言的人名前面也带上头像 ✓
                                  —— 群里一屏十几条，光看名字分不出谁是谁 ✓ 有头像一眼就分得开 ✓
                                  （"我"和"系统"不加 ✗ 它们不是员工 ✓ 加了反而乱 ✓）*/}
                              {!mine && who !== '系统' && <Avatar name={who} size={16} />}
                              {who}
                              {m.status && m.status !== 'running' && <span style={{ marginLeft: 6, opacity: 0.75 }}>{m.status === 'done' ? '✅ 已交付' : m.status === 'partial' ? '🟡 部分完成' : m.status === 'failed' ? '❌ 失败' : m.status}</span>}
                              {hhmm && <span style={{ marginLeft: 6, fontWeight: 400 }}>{hhmm}</span>}
                            </div>
                            {/* ★ 2026-10-06（用户实测："我不知道在哪打开"）：**产物放最上面**。
                                原来文件块在消息**底部**（而且长交付还会被折叠挡住 ✗）⇒ 用户根本找不到 ✗。
                                现在：交付消息**顶部**先给一排可点的产物，带「打开」字样与个数 ✓ */}
                            {!!m.attachments?.length && (
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', marginBottom: 6 }}>
                                <span style={{ fontSize: 12, opacity: 0.75 }}>📦 产物 {m.attachments.length} 个：</span>
                                {m.attachments.map((a) => (
                                  // ★★ 2026-10-06（用户实测：**点产物弹「需要访问密码（token 不匹配）」** ✗✗）：
                                  //   取工作区文件**要鉴权** ✓ 而浏览器新窗口**发不了自定义请求头** ✗
                                  //   ⇒ 必须把 `?token=` 拼进 URL ✓（项目里早有统一函数 `authedUrl` ✓
                                  //   对话附件 / 正文图片 / 产物面板 / 灯箱四处都在用 ✓ **群聊这两处漏了** ✗）。
                                  //   这毛病在任务页犯过一次 ✓ 当时加了红绿组 ✓ 但**没覆盖群聊这条路** ✓
                                  //   ⇒ 这次一并补测试 ✓ 免得再犯第三次 ✓。
                                  // ★★ 而且（用户提的第二件事 ✓）：**点了能在群里直接看** ✓ ——
                                  //   原来只能新窗口开原始文件 ✗ 还得跳去任务页 ✗
                                  //   ⇒ 左键点它**就地弹预览**（复用任务页那个产物面板 ✓ 不重写 ✓）；
                                  //     想在新窗口打开/下载的，**按住 Ctrl 点或右键**照旧走原来的链接 ✓。
                                  <a key={a} className="filechip" title={`点开预览 ${a}（想在新窗口开：按住 Ctrl 点）`}
                                    href={authedUrl(`${API_BASE}/tasks/${m.task_id}/files/raw?path=${encodeURIComponent(a)}`)}
                                    target="_blank" rel="noreferrer"
                                    onClick={(ev) => {
                                      if (ev.ctrlKey || ev.metaKey || ev.shiftKey) return;   // 让人还能新窗口开 ✓
                                      ev.preventDefault();
                                      setArtTask(m.task_id ?? '');
                                      setArtFile(a);
                                    }}>
                                    📎 {a} ↗
                                  </a>
                                ))}
                                {/* ★ 2026-10-06（用户："我不知道在哪打开"）：产物里只要有网页，
                                    就给一个**显眼的「打开看看」** —— 点它直接新窗口跑起来 ✓ */}
                                {m.attachments.filter((a) => /\.html?$/i.test(a)).slice(0, 1).map((a) => (
                                  <a key={'open-' + a} className="btn-mini primary" style={{ textDecoration: 'none' }}
                                    href={authedUrl(`${API_BASE}/tasks/${m.task_id}/files/raw?path=${encodeURIComponent(a)}`)}
                                    target="_blank" rel="noreferrer" title={`在浏览器里打开 ${a}`}>
                                    ▶ 打开看看
                                  </a>
                                ))}
                              </div>
                            )}
                            {/* ★ 2026-10-06：交付里那节「怎么打开 / 怎么用」单独拎出来显示一行 ——
                                用户要的就是这句话，不该埋在长文里 ✗（小程序要开发者工具、App 要构建…） */}
                            {howToOpen(String(m.text ?? '')) && (
                              <div style={{ background: 'rgba(79,142,247,0.14)', borderRadius: 8,
                                            padding: '5px 9px', marginBottom: 6, fontSize: 12.5 }}>
                                🖱️ {howToOpen(String(m.text ?? ''))}
                              </div>
                            )}
                            {/* ★ 2026-10-06：验收结论渲染成醒目结论条（✅/❌/🟡 开头才走这里） */}
                            <VerdictCard text={String(m.text ?? '')} />
                            {/* ★★ 2026-10-06（真 bug，截图里逮到的）：结论条**已经把整条文本渲染了一遍** ✓，
                                这里再渲染一次就成了"同一段话显示两遍" ✗（用户截图上明明白白 ✓）。
                                所以：走结论条的消息**不再走下面那套**（小节导航 + 折叠正文）✓。 */}
                            {!isVerdict(String(m.text ?? '')) && (<>
                            {/* 交付消息：顶部给一节导航（只看小节名就知道它交了什么） */}
                            {sectionNames(String(m.text ?? '')).length > 0 && (
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 4 }}>
                                {sectionNames(String(m.text ?? '')).map((s) => (
                                  <span key={s} className="filechip" style={{ cursor: 'default' }}>{s}</span>
                                ))}
                              </div>
                            )}
                            {/* ★ 2026-10-06 体验：长交付**默认折叠**（群里不再刷一屏长文 ✗）。
                                实测：一条交付能写 2000+ 字，几个人一交，群里就全是字墙。
                                折叠只影响**显示**，一字不改内容 ✓（点「展开全文」看完整）。 */}
                            <CollapsibleDelivery text={String(m.text ?? '')} />
                            </>)}
                            {/* ★ 接着跑（P0-4）：失败/部分完成的交付旁边给一个按钮 ——
                                同一个任务、同一个工作区继续，不从头烧钱
                                （Anthropic 复盘："错误会累积，要从出错处续跑"） */}
                            {!!m.task_id && (m.status === 'failed' || m.status === 'partial') && (
                              <div className="resume-row">
                                <button
                                  className="btn-mini"
                                  title="带着工作区里已有的产物继续（不是重新开始）"
                                  onClick={() => {
                                    void api.teamResume(gid ?? '', String(m.task_id), who)
                                      .then(() => loadAll())
                                      .catch((e) => setGModeErr(e instanceof Error ? e.message : String(e)));
                                  }}
                                >
                                  🔁 接着跑
                                </button>
                              </div>
                            )}
                            {/* ★ 群里的审批卡片（2026-10-05 用户要求）：他们的活、要点的允许，
                                都该在群里就地批，不用跑去任务页。语义与任务页一致（后端共用一份）。 */}
                            {!!m.approval?.call_id && (
                              <div className="approval-card">
                                <div className="approval-title">
                                  需要你批准：{m.approval.tool || '动作'}
                                </div>
                                {!!m.approval.detail && <div className="approval-detail mono">{m.approval.detail}</div>}
                                {handled[m.approval.call_id] ? (
                                  <div className="approval-done">已处理：{handled[m.approval.call_id]}</div>
                                ) : (
                                  <div className="approval-actions">
                                    <button className="btn-mini primary" disabled={!!apBusy}
                                      onClick={() => void doApprove(m, 'once')}>允许一次</button>
                                    <button className="btn-mini" disabled={!!apBusy}
                                      onClick={() => void doApprove(m, 'always')}>本任务都允许</button>
                                    <button className="btn-mini" disabled={!!apBusy}
                                      title="这个任务的命令不再逐条询问（含 $()、heredoc 这类）；后端重启后失效"
                                      onClick={() => void doApprove(m, 'all')}>本任务全部允许</button>
                                    {/* ★ 2026-10-06「这类以后都别问」：跨任务、后端落盘（重启仍在 ✓）。
                                        实测一个 10 分钟的小活要点 2–5 次审批 ⇒ 等审批比干活还久 ✗。
                                        安全：删除/移动/覆盖写 ✗ 与 $()、管道这类结构 ✗ **永远不会**被它放行 ✓。 */}
                                    <button className="btn-mini" disabled={!!apBusy}
                                      title="记住这个程序（如 python / pytest / git），以后新任务也不再问；删除、移动、$()／管道这类永远不会被它放行，随时可在设置里收回"
                                      onClick={() => void doApprove(m, 'forever')}>这类以后都别问</button>
                                    <button className="btn-mini danger" disabled={!!apBusy}
                                      onClick={() => void doApprove(m, 'deny')}>拒绝</button>
                                  </div>
                                )}
                              </div>
                            )}
                            {/* （§6.7 的附件块已**移到消息顶部** —— 2026-10-06 用户实测：
                                放底部时，长交付一折叠就把它挡住了，用户根本找不到 ✗） */}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  <div style={{ position: 'relative', padding: '8px 14px', borderTop: '1px solid rgba(255,255,255,0.08)', display: 'flex', gap: 8 }}>
                    {/* @ 提示：输入 @ 弹出成员名单，点击补全（广播/点名模式都能用） */}
                    {mentionCandidates.length > 0 && (
                      <div style={{ position: 'absolute', bottom: '100%', left: 14, marginBottom: 4, background: '#161b26', border: '1px solid var(--border)', borderRadius: 8, padding: 4, minWidth: 160, boxShadow: '0 6px 24px rgba(0,0,0,0.4)' }}>
                        {mentionCandidates.map((e, i) => (
                          <div key={e.id} onMouseDown={(ev) => { ev.preventDefault(); insertMention(e.name); }} style={{ padding: '5px 9px', borderRadius: 6, cursor: 'pointer', fontSize: 12.5, background: i === mentionIdx ? 'rgba(107,163,245,0.18)' : 'transparent' }}>
                            @{e.name} <span style={{ opacity: 0.45, fontSize: 11 }}>{e.role}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {/* ★ 成员一键插入（2026-10-05）：@ 自动补全早就有，但要先知道名字才打得出来。
                        这里给一个常驻按钮：点开就是本群成员名单，点谁插谁。
                        广播模式插 @ 没意义（全员都收到），所以只在点名/组长模式下给。 */}
                    {curGroup.mode !== 'broadcast' && (
                      <div style={{ position: 'relative' }}>
                        <button
                          className="btn-mini"
                          title="点名：从本群成员里选一个（等于 @他的名字）"
                          onClick={() => setPickOpen((v) => !v)}
                        >
                          @
                        </button>
                        {pickOpen && (
                          <div className="member-pick">
                            {employees.filter((e) => curGroup.members.includes(e.id)).map((e) => (
                              <div
                                key={e.id}
                                className="member-pick-item"
                                onMouseDown={(ev) => {
                                  ev.preventDefault();
                                  setInput((prev) => prev + (prev && !prev.endsWith(' ') ? ' ' : '') + '@' + e.name + ' ');
                                  setPickOpen(false);
                                  setMentionAt(null);
                                }}
                              >
                                @{e.name} <span style={{ opacity: 0.45, fontSize: 11 }}>{e.role || e.dept}</span>
                              </div>
                            ))}
                            {employees.filter((e) => curGroup.members.includes(e.id)).length === 0 && (
                              <div className="member-pick-item" style={{ opacity: 0.5 }}>这个群还没有成员</div>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                    {/* ★ 语音输入：与首页/任务内同一套实现 */}
                    <button
                      className={`btn-mini ${voice.on ? 'voice-on' : ''}`}
                      aria-label="语音输入"
                      title={voice.on ? '停止并填入输入框' : '语音输入：点一下开始、再点一下停止；也可以按住右 Ctrl 说话'}
                      onClick={() => voice.toggle()}
                    >
                      {voice.on ? <Square size={13} /> : <Mic size={13} />}
                    </button>
                    <input style={{ flex: 1, padding: '8px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'rgba(255,255,255,0.06)', color: 'inherit', fontSize: 13 }} value={input} placeholder={curGroup.mode === 'broadcast' ? '广播：全员都会收到并执行…' : curGroup.mode === 'leader' ? '给组长下达目标，组长拆解分派…' : curGroup.mode === 'relay' ? '说出目标，成员按顺序接力…' : curGroup.mode === 'meeting' ? '给个议题，他们开会讨论（可写「3轮」）…' : '输入消息，@名字 指派…'} onChange={(e) => onInputChange(e.target.value)} onKeyDown={(e) => {
                      if (mentionCandidates.length && e.key === 'ArrowDown') { e.preventDefault(); setMentionIdx((i) => Math.min(i + 1, mentionCandidates.length - 1)); return; }
                      if (mentionCandidates.length && e.key === 'ArrowUp') { e.preventDefault(); setMentionIdx((i) => Math.max(i - 1, 0)); return; }
                      if (mentionCandidates.length && e.key === 'Enter') { e.preventDefault(); insertMention(mentionCandidates[mentionIdx]?.name || ''); return; }
                      if (e.key === 'Enter' && input.trim()) say();
                    }} />
                    <button className="send-btn" disabled={!input.trim()} onClick={say}><Send size={15} /></button>
                    {voice.hint && (
                      <div className="art-note" style={{ position: 'absolute', bottom: '100%', right: 14, marginBottom: 4 }}>
                        {voice.hint}
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', opacity: 0.3 }}>← 选一个群开始聊天</div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ★ 2026-10-06（用户提的）：「点了产物，在群里直接看」✓ ——
          整层弹出来，里面**复用任务页那个产物面板**（图/网页/文本/表格/视频它都认 ✓）。
          样式注意：**不用 backdrop-filter** ✗（那是已知的整屏变黑诱因 ✓ 有专门的守卫测试 ✓）。 */}
      {!!artTask && (
        <div role="dialog" aria-modal="true" aria-label="产物预览"
          onClick={() => setArtTask(null)}
          style={{ position: 'fixed', inset: 0, zIndex: 300, background: 'rgba(6,9,14,0.78)',
                   display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
          <div onClick={(ev) => ev.stopPropagation()}
            style={{ width: 'min(1080px, 96vw)', maxHeight: '88vh', overflow: 'auto',
                     background: 'var(--card)', border: '1px solid var(--border)',
                     borderRadius: 12, padding: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <b style={{ flex: 1 }}>📦 这一步做出来的东西</b>
              <button className="btn-mini" onClick={() => setArtTask(null)}>关闭</button>
            </div>
            <ArtifactPanel taskId={artTask} embedded initialOpen={artFile} />
            <div style={{ fontSize: 11, opacity: 0.5, marginTop: 8 }}>
              点文件名就地看 ✓ 想在新窗口打开或下载，点产物时**按住 Ctrl** ✓
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
