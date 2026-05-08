import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      outDir: 'out/electron',
      rollupOptions: {
        input: {
          main: resolve(__dirname, 'electron/main.ts'),
        },
      },
    },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      outDir: 'out/preload',
      rollupOptions: {
        input: {
          preload: resolve(__dirname, 'electron/preload.ts'),
        },
      },
    },
  },
  renderer: {
    root: '.',
    build: {
      outDir: 'out/renderer',
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'index.html'),
          loading: resolve(__dirname, 'loading.html'),
        },
      },
    },
    plugins: [react()],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src'),
      },
    },
    server: {
      proxy: {
        // OpenAI
        '/proxy/openai': {
          target: 'https://api.openai.com',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/openai/, ''),
        },
        // Anthropic
        '/proxy/anthropic': {
          target: 'https://api.anthropic.com',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/anthropic/, ''),
        },
        // DeepSeek
        '/proxy/deepseek': {
          target: 'https://api.deepseek.com',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/deepseek/, ''),
        },
        // MiniMax
        '/proxy/minimax': {
          target: 'https://api.minimax.chat',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/minimax/, ''),
        },
        // Moonshot
        '/proxy/moonshot': {
          target: 'https://api.moonshot.cn',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/moonshot/, ''),
        },
        // Qwen (Aliyun)
        '/proxy/qwen': {
          target: 'https://dashscope.aliyuncs.com',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/qwen/, ''),
        },
        // Doubao (Volcengine)
        '/proxy/doubao': {
          target: 'https://ark.cn-beijing.volces.com',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/doubao/, ''),
        },
        // Zhipu
        '/proxy/zhipu': {
          target: 'https://open.bigmodel.cn',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/zhipu/, ''),
        },
        // Gemini
        '/proxy/gemini': {
          target: 'https://generativelanguage.googleapis.com',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/gemini/, ''),
        },
        // Groq
        '/proxy/groq': {
          target: 'https://api.groq.com',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/groq/, ''),
        },
        // Mistral
        '/proxy/mistral': {
          target: 'https://api.mistral.ai',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/mistral/, ''),
        },
        // xAI
        '/proxy/xai': {
          target: 'https://api.x.ai',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/xai/, ''),
        },
        // OpenRouter
        '/proxy/openrouter': {
          target: 'https://openrouter.ai',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/proxy\/openrouter/, ''),
        },
      },
    },
  },
})
