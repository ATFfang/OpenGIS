/// <reference types="vite/client" />
/// <reference types="vitest/globals" />

declare module '*?worker' {
  const WorkerFactory: {
    new (): Worker
  }
  export default WorkerFactory
}
