import fs from 'node:fs'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

interface DevToolsTarget {
  type: string
  webSocketDebuggerUrl: string
}

interface DevToolsReply<Value> {
  id: number
  result?: { exceptionDetails?: object; result?: { value?: Value } }
}

interface CallParams {
  url?: string
  expression?: string
  returnByValue?: boolean
  awaitPromise?: boolean
}

async function readPort(profile: string): Promise<string | undefined> {
  try {
    return (await Bun.file(`${profile}/DevToolsActivePort`).text()).split('\n')[0]?.trim()
  } catch {
    return undefined
  }
}

async function listTargets(port: string): Promise<DevToolsTarget[] | undefined> {
  try {
    return JSON.parse(await (await fetch(`http://127.0.0.1:${port}/json/list`)).text())
  } catch {
    return undefined
  }
}

async function poll<Value>(attempts: number, read: () => Promise<Value | undefined>): Promise<Value | undefined> {
  for (let attempt = 0; attempt < attempts; attempt++) {
    const value = await read()
    if (value) return value
    await Bun.sleep(100)
  }
  return undefined
}

async function connect(profile: string): Promise<WebSocket> {
  const port = await poll(100, () => readPort(profile))
  if (!port) throw new Error(`Chrome wrote no DevToolsActivePort in ${profile}`)
  const targets = await poll(50, () => listTargets(port))
  const page = targets?.find((target) => target.type === 'page')
  if (!page) throw new Error('Chrome exposed no page target')
  const socket = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve) => socket.addEventListener('open', resolve))
  return socket
}

export class DevToolsPage {
  readonly #socket: WebSocket
  readonly #pending = new Map<number, (text: string) => void>()
  #nextId = 1

  constructor(socket: WebSocket) {
    this.#socket = socket
    socket.addEventListener('message', (event) => {
      const text = String(event.data)
      const { id }: { id: number } = JSON.parse(text)
      const settle = this.#pending.get(id)
      this.#pending.delete(id)
      settle?.(text)
    })
  }

  call<Value>(method: string, params: CallParams = {}): Promise<DevToolsReply<Value>> {
    return new Promise((resolve) => {
      const id = this.#nextId++
      this.#pending.set(id, (text) => resolve(JSON.parse(text)))
      this.#socket.send(JSON.stringify({ id, method, params }))
    })
  }

  async evaluate<Value>(expression: string): Promise<Value | undefined> {
    const reply = await this.call<Value>('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (reply.result?.exceptionDetails) throw new Error(JSON.stringify(reply.result.exceptionDetails))
    return reply.result?.result?.value
  }
}

export async function withChrome<Outcome>(profile: string, flags: string[], use: (page: DevToolsPage) => Promise<Outcome>): Promise<Outcome> {
  const chrome = Bun.spawn([CHROME, '--headless=new', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${profile}`, ...flags, 'about:blank'], { stdout: 'ignore', stderr: 'ignore' })
  let socket: WebSocket | undefined
  try {
    socket = await connect(profile)
    return await use(new DevToolsPage(socket))
  } finally {
    socket?.close()
    chrome.kill()
    await chrome.exited
    fs.rmSync(profile, { recursive: true, force: true })
  }
}
