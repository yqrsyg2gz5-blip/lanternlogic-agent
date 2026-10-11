import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 开发服务器：5173；/api 代理到未来本地后端 8642（契约二）
// Phase 1 用 Mock 事件流时代理不生效也无妨；Phase 2 后端起来后自动切换
export default defineConfig({
  plugins: [react()],
  build: {
    // ★ 2026-10-06：**分包**（此前整包一个 512KB 的 js，构建每次都警告 ✗）。
    //   把"很少变"的大依赖单独切出去：浏览器能长期缓存它们 ⇒ 我们自己改代码时
    //   用户只需要重新下那小块 ✓（也是"大厂感"里很实在的一条：首屏更快 ✓）。
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          markdown: ['react-markdown', 'remark-gfm'],
          icons: ['lucide-react'],
          qr: ['qrcode', 'jsqr'],
        },
      },
    },
    chunkSizeWarningLimit: 700,
  },
  server: {
    port: 5173,
    // 端口被占用时直接失败，不要静默改用 5174：
    // 否则 start.bat 打开的仍是旧实例，用户以为重启了其实没有（2026-09-30 实际踩到）
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8642',
        changeOrigin: true,
      },
    },
  },
});
