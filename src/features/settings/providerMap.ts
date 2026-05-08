import type { ProtocolType } from '@/stores/settingsStore'

/* ── Eagerly load all provider SVG icons as raw strings ── */
const iconModules = import.meta.glob<string>('../../assets/icons/*.svg', {
  query: '?raw',
  import: 'default',
  eager: true,
})

/** provider id → raw SVG markup */
export const iconMap: Record<string, string> = {}
for (const [path, svg] of Object.entries(iconModules)) {
  const name = path.split('/').pop()!.replace('.svg', '')
  iconMap[name] = svg
}

/* ── Provider default configurations ── */

export interface ProviderConfig {
  id: string
  label: string
  protocol: ProtocolType
  baseURL: string
  defaultModel: string
}

export const PROVIDERS: ProviderConfig[] = [
  { id: 'openai',       label: 'OpenAI',         protocol: 'openai',     baseURL: 'https://api.openai.com/v1',              defaultModel: 'gpt-4o' },
  { id: 'anthropic',    label: 'Anthropic',       protocol: 'anthropic',  baseURL: 'https://api.anthropic.com',              defaultModel: 'claude-sonnet-4-20250514' },
  { id: 'deepseek',     label: 'DeepSeek',        protocol: 'openai',     baseURL: 'https://api.deepseek.com',               defaultModel: 'deepseek-chat' },
  { id: 'minimax',      label: 'MiniMax',         protocol: 'anthropic',  baseURL: 'https://api.minimaxi.com/anthropic',     defaultModel: 'MiniMax-M2.7' },
  { id: 'mistral',      label: 'Mistral',         protocol: 'openai',     baseURL: 'https://api.mistral.ai/v1',              defaultModel: 'mistral-large-latest' },
  { id: 'google',       label: 'Google Gemini',   protocol: 'openai',     baseURL: 'https://generativelanguage.googleapis.com/v1beta/openai/', defaultModel: 'gemini-2.0-flash' },
  { id: 'xai',          label: 'xAI Grok',        protocol: 'openai',     baseURL: 'https://api.x.ai/v1',                   defaultModel: 'grok-3' },
  { id: 'ollama',       label: 'Ollama',          protocol: 'openai',     baseURL: 'http://localhost:11434/v1',              defaultModel: 'llama3' },
  { id: 'openrouter',   label: 'OpenRouter',      protocol: 'openai',     baseURL: 'https://openrouter.ai/api/v1',           defaultModel: 'meta-llama/llama-3-70b-instruct' },
  { id: 'cohere',       label: 'Cohere',          protocol: 'openai',     baseURL: 'https://api.cohere.ai/v1',               defaultModel: 'command-r-plus' },
  { id: 'azure',        label: 'Azure OpenAI',    protocol: 'openai',     baseURL: '',                                       defaultModel: 'gpt-4o' },
  { id: 'baidu',        label: 'Baidu Wenxin',    protocol: 'openai',     baseURL: 'https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop', defaultModel: 'ernie-4.0' },
  { id: 'zhipu',        label: 'Zhipu GLM',       protocol: 'openai',     baseURL: 'https://open.bigmodel.cn/api/paas/v4',   defaultModel: 'glm-4' },
  { id: 'hunyuan',      label: 'Tencent Hunyuan', protocol: 'openai',     baseURL: 'https://api.hunyuan.cloud.tencent.com/v1', defaultModel: 'hunyuan-pro' },
  { id: 'qwen',         label: 'Alibaba Qwen',    protocol: 'openai',     baseURL: 'https://dashscope.aliyuncs.com/compatible-mode/v1', defaultModel: 'qwen-max' },
  { id: 'doubao',       label: 'ByteDance Doubao',protocol: 'openai',     baseURL: 'https://ark.cn-beijing.volces.com/api/v3', defaultModel: 'doubao-pro-256k' },
  { id: 'kimi',         label: 'Kimi (Moonshot)', protocol: 'openai',     baseURL: 'https://api.moonshot.cn/v1',             defaultModel: 'moonshot-v1-128k' },
  { id: 'stepfun',      label: 'StepFun',         protocol: 'openai',     baseURL: 'https://api.stepfun.com/v1',             defaultModel: 'step-2-16k' },
  { id: 'yi',           label: '01.AI Yi',        protocol: 'openai',     baseURL: 'https://api.lingyiwanwu.com/v1',         defaultModel: 'yi-large' },
  { id: 'siliconflow',  label: 'SiliconFlow',     protocol: 'openai',     baseURL: 'https://api.siliconflow.cn/v1',          defaultModel: 'Qwen/Qwen2.5-72B-Instruct' },
  { id: 'xiaomimimo',   label: 'Xiaomi MiMo',     protocol: 'openai',     baseURL: 'https://api.xiaomimimo.com/v1',          defaultModel: 'MiMo-7B-RL' },
  { id: 'perplexity',   label: 'Perplexity',      protocol: 'openai',     baseURL: 'https://api.perplexity.ai',              defaultModel: 'llama-3.1-sonar-huge-128k-online' },
  { id: 'nvidia',       label: 'NVIDIA NIM',      protocol: 'openai',     baseURL: 'https://integrate.api.nvidia.com/v1',    defaultModel: 'meta/llama-3.1-405b-instruct' },
  { id: 'huggingface',  label: 'Hugging Face',    protocol: 'openai',     baseURL: 'https://api-inference.huggingface.co/models', defaultModel: 'meta-llama/Llama-3.1-70B-Instruct' },
]
