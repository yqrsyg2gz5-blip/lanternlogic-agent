import { useCallback, useEffect, useState } from 'react';
import { Hero } from './components/Hero';
import { api } from './api';
import type { TaskSummary } from './types';
import { Sidebar } from './components/Sidebar';
import { TaskView } from './components/TaskView';
import { PreviewOverlay } from './components/PreviewOverlay';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ChatProto } from './components/ChatProto';

export default function App() {
  // ★ Phase 3 ⑦：对话界面**原型**开关（?proto=chat）—— 只影响这一个查询参数，
  //   不调后端、不写状态；把这三行连同 ChatProto.tsx 删掉即可完全回退。
  const [proto, setProto] = useState(() => new URLSearchParams(window.location.search).get('proto') === 'chat');
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [currentId, setCurrentId] = useState<string | null>(null);
  // 手机直连：访问密码浮层（后端局域网模式且 401 时出现，第 41 班）
  const [needToken, setNeedToken] = useState(false);
  const [tokenInput, setTokenInput] = useState('');
  // 复审 P0-C：≤760px 侧栏变抽屉，汉堡按钮唤出（此前浮层压住正文且关不掉）
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const refreshTasks = useCallback(() => {
    void api.listTasks().then((d) => { setTasks(d); setTasksLoading(false); }).catch(() => setTasksLoading(false));
  }, []);

  useEffect(() => {
    // 原型模式（?proto=chat）下不拉业务数据：原型要能**脱离后端**单独看
    //（设计评审时不必先起服务），也避免"原型偷偷依赖接口"。
    if (proto) return;
    refreshTasks();
  }, [refreshTasks, proto]);

  // 启动探测：401 = 局域网模式且密码未通过 → 弹密码浮层
  // ★ 2026-10-07：这两处**故意不改成 api.*** ✓ —— 它们问的就是"密码对不对"本身：
  //   · 这里带的是 localStorage 里**已有的**那个（要判它还有效吗）
  //   · 下面 submitToken 带的更是不可能在 localStorage 里的**正在试的那个**
  //   而 `request()` 只会带 localStorage 那份 ⇒ 换成它反而测不出"密码错"✗。
  useEffect(() => {
    if (proto) return;
    void fetch('/api/v1/auth/check' + (localStorage.getItem('authToken') ? `?token=${encodeURIComponent(localStorage.getItem('authToken') ?? '')}` : ''))
      .then((r) => { if (r.status === 401) setNeedToken(true); })
      .catch(() => undefined);
  }, [proto]);

  // ★★ 2026-10-07：**主题在启动时就应用** ✓（用户："主题最起码有两个 —— 深色 + 白色"✓）
  //   在此之前 `ui.theme` 只是"设置页里显示的一行字" ✗ 从来没真作用到界面上 ✓
  //   两道：
  //     ① **先用 localStorage 里那份**（上次选的 ✓ 立刻生效 ✓ 不等网络 ✓ 不会闪一下白 ✗）
  //     ② 再问后端要一次（换台设备/清过缓存时也对 ✓）
  useEffect(() => {
    const local = localStorage.getItem('theme');
    if (local === 'light' || local === 'dark') {
      document.documentElement.dataset.theme = local;
    } else {
      document.documentElement.dataset.theme = 'dark';   // 默认深色 ✓（与 config 默认一致 ✓）
    }
    // ★ 2026-10-07：原来这里自己拼 `?token=` + 自己判 `r.ok` ✗ —— 改走 api 层统一带密码 ✓
    //   （请求失败照样静默：主题读不到就用 localStorage 那份 ✓ 不该因此打扰用户 ✓）
    void api.getSettings()
      .catch(() => null)
      .then((s) => {
        const t = (s as { ui?: { theme?: string } } | null)?.ui?.theme;
        if ((t === 'light' || t === 'dark') && t !== localStorage.getItem('theme')) {
          localStorage.setItem('theme', t);
          document.documentElement.dataset.theme = t;
        }
      });
  }, [proto]);

  const submitToken = (): void => {
    // ★★ 2026-10-07（第 5 项）：后端加了**防爆破限流**（连错太多次 ⇒ 429 + 等 N 秒）✓
    //   而这里原来**不管什么原因**都只弹一句"密码不正确" ✗ —— 429 时那是**假话** ✓
    //   用户会以为密码打错了、接着敲 ✓ 越敲锁得越久 ✓（正是最不该发生的那种循环 ✗）
    //   ⇒ 现在把后端**原话**显示出来 ✓（"密码错了太多次，请等 47 秒后再试"✓
    //      —— 具体数字，用户照着等就行 ✓）
    void fetch(`/api/v1/auth/check?token=${encodeURIComponent(tokenInput)}`)
      .then(async (r) => {
        if (r.ok) {
          localStorage.setItem('authToken', tokenInput);
          setNeedToken(false);
          window.location.reload();
          return;
        }
        let detail = '';
        try {
          detail = ((await r.json()) as { detail?: string }).detail ?? '';
        } catch {
          /* 不是 JSON 就别硬解 */
        }
        if (r.status === 429) {
          alert(detail || '密码错了太多次，请等一会儿再试（防爆破限流）');
        } else {
          alert(detail || '密码不正确');
        }
      });
  };

  const handleDeleteTask = useCallback((taskId: string) => {
    void api.deleteTask(taskId).then(() => {
      setTasks((prev) => prev.filter((t) => t.id !== taskId));
      setCurrentId((cur) => (cur === taskId ? null : cur));
    });
  }, []);

  return (
    <div className="app">
      {proto && <ChatProto onExit={() => setProto(false)} />}
      {/* ★ 图片灯箱：全局委托（对话/附件/正文/产物面板的图都能点开放大） */}
      {!proto && <PreviewOverlay />}
      {!proto && <>
      {needToken && (
        <div className="token-mask">
          <div className="token-box">
            <b style={{ fontSize: 15 }}>🔒 访问密码</b>
            <p style={{ fontSize: 12.5, opacity: 0.7, margin: '8px 0 12px' }}>本 Agent 处于局域网可访问模式，请输入访问密码（config.json 的 server.access_token）。</p>
            <input style={{ width: '100%', boxSizing: 'border-box', padding: '8px 10px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.2)', background: 'transparent', color: 'inherit' }} type="password" value={tokenInput} autoFocus
              onChange={(e) => setTokenInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') submitToken(); }} />
            <button className="btn-primary" style={{ width: '100%', marginTop: 10 }} onClick={submitToken}>进入</button>
          </div>
        </div>
      )}
      <button
        className="sidebar-toggle"
        aria-label="打开侧栏菜单（含团队/设置入口）"
        onClick={() => setMobileNavOpen((v) => !v)}
      >
        ☰
      </button>
      {/* 验证报告 24 第6条：抽屉遮罩（点外侧收起；此前点外不关、下层可交互） */}
      <div
        className={`sidebar-mask ${mobileNavOpen ? 'mask-on' : ''}`}
        onClick={() => setMobileNavOpen(false)}
      />
      <Sidebar
        tasks={tasks}
        loading={tasksLoading}
        currentId={currentId}
        mobileOpen={mobileNavOpen}
        onNavigate={() => setMobileNavOpen(false)}
        onSelect={(id) => { setMobileNavOpen(false); setCurrentId(id); }}
        onDeleteTask={handleDeleteTask}
       onNewChat={() => setCurrentId(null)}
        onRename={(id, title) => { void api.renameTask(id, title).then(refreshTasks); }}
        onPin={(id, pinned) => { void api.pinTask(id, pinned).then(refreshTasks); }}
 />
      {currentId ? (
        // ★ 任务视图单独兜一层：里面卡片多、又常有流式更新，一个卡片崩了不该拖垮整个应用
        //   （用户实测"点允许一次整页变黑"就是渲染异常卸载整棵树的样子）
        <ErrorBoundary label="任务视图" onReset={() => setCurrentId(currentId)}>
          <TaskView key={currentId} taskId={currentId} onTasksChanged={refreshTasks} />
        </ErrorBoundary>
      ) : (
        // 空态：Hero 里建完任务必须**立刻跳到那个任务**
        //（用户反馈：原来建完还停在欢迎页，得手动点右侧才看得见 —— 真 bug）
        <Hero
          onCreated={(id) => {
            refreshTasks();
            setCurrentId(id);
          }}
        />
      )}
      </>}
    </div>
  );
}
