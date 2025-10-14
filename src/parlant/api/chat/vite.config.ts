import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const isDev = mode === 'development';
  
  return {
    base: '/chat/',
    test: {
      globals: true,
      environment: 'jsdom',
      includeSource: ['app/**/*.{jsx,tsx}'],
      setupFiles: ['./setupTests.ts']
    },
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    // 定义环境变量
    define: {
      // 在开发环境中设置 VITE_BASE_URL 为 /api，生产环境中为空
      'import.meta.env.VITE_BASE_URL': JSON.stringify(isDev ? '/api' : ''),
    },
    server: {
      port: 8002,
      host: '127.0.0.1',
      proxy: {
        // 代理所有 /api 请求到后端服务（包括 WebSocket）
        '/api': {
          target: 'http://127.0.0.1:8800',
          changeOrigin: true,
          secure: false,
          ws: true,  // 支持 WebSocket 升级
          rewrite: (path) => path.replace(/^\/api/, '')
        },
        // 代理 /logs WebSocket 连接 - 使用 http:// 而不是 ws://
        '/logs': {
          target: 'http://127.0.0.1:8800',
          ws: true,
          changeOrigin: true
        },
        // 代理其他 WebSocket 连接
        '/ws': {
          target: 'http://127.0.0.1:8800',
          ws: true,
          changeOrigin: true
        }
      }
    }
  };
});
