#!/usr/bin/env node
// Small, dependency-free VS Code UI driver. Requires Node 22+.
import { writeFile } from 'node:fs/promises';

const [action = 'help', ...args] = process.argv.slice(2);
if (action === 'help') {
  console.log(`Usage: node tools/vscode-ui.mjs ACTION [ARGS]
  windows                  List window IDs and titles
  snapshot                 Read visible text and accessible controls
  key Ctrl+Shift+p          Press a key/chord (also F5, F9, F10, F11)
  text 'text'              Type into the focused control
  click 'CSS selector'     Click one visible matching element
  eval 'JS expression'     Inspect/interact with the renderer DOM
  screenshot /tmp/ide.png  Save the current window
Set PODBENCH_VSCODE_PORT (default 9222) and, when there is more than one
window, PODBENCH_VSCODE_WINDOW to an exact window ID from 'windows'.
Start VS Code using tools/vscode-code, or pass that wrapper to
podbench ide vscode POD -n NAMESPACE --code "$PWD/tools/vscode-code".
The debugging port grants control of the IDE; keep it local and close the
dedicated VS Code instance when finished.`);
  process.exit(0);
}
let socket;
try {
  const port = Number(process.env.PODBENCH_VSCODE_PORT || 9222);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw Error('Invalid port');
  const response = await fetch(`http://127.0.0.1:${port}/json/list`, {
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw Error(`DevTools returned HTTP ${response.status}`);
  const windows = (await response.json()).filter(w => w.type === 'page'
    && w.url.startsWith('vscode-file:'));
  if (action === 'windows') {
    console.log(JSON.stringify(windows.map(({ id, title, url }) => ({ id, title, url })), null, 2));
    process.exit(0);
  }
  const selected = process.env.PODBENCH_VSCODE_WINDOW;
  const matches = selected ? windows.filter(w => w.id === selected) : windows;
  if (matches.length !== 1) throw Error('Select one window with PODBENCH_VSCODE_WINDOW; run windows first');
  socket = new WebSocket(matches[0].webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error('DevTools connection timed out')), 5000);
    socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
    socket.addEventListener('error', () => { clearTimeout(timer); reject(Error('DevTools connection failed')); }, { once: true });
  });
  let sequence = 0;
  function send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => finish(Error(`${method} timed out`)), 10000);
      function finish(error, result) {
        clearTimeout(timer);
        socket.removeEventListener('message', receive);
        error ? reject(error) : resolve(result);
      }
      function receive(event) {
        const message = JSON.parse(event.data);
        if (message.id === id) finish(message.error && Error(message.error.message), message.result);
      }
      socket.addEventListener('message', receive);
      socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async function evaluate(expression) {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  }
  const required = () => { if (!args.length) throw Error(`${action} requires an argument`); return args.join(' '); };
  if (['key', 'text', 'click'].includes(action)) await send('Page.bringToFront');
  if (action === 'snapshot') {
    console.log(JSON.stringify(await evaluate(`({
      title: document.title,
      text: document.body.innerText,
      controls: [...document.querySelectorAll('[aria-label], input, [role="button"]')]
        .filter(e => e.getBoundingClientRect().width && e.getBoundingClientRect().height)
        .map(e => ({tag: e.tagName, role: e.getAttribute('role'), label: e.getAttribute('aria-label'),
          placeholder: e.getAttribute('placeholder'), value: e.value}))
    })`), null, 2));
  } else if (action === 'eval') {
    console.log(JSON.stringify(await evaluate(required()), null, 2));
  } else if (action === 'text') {
    await send('Input.insertText', { text: required() });
  } else if (action === 'key') {
    const parts = required().split('+');
    const key = parts.pop();
    const flags = { Alt: 1, Ctrl: 2, Meta: 4, Shift: 8 };
    let modifiers = 0;
    for (const part of parts) {
      if (!(part in flags)) throw Error(`Unknown modifier ${part}`);
      modifiers |= flags[part];
    }
    const special = { Enter: 13, Escape: 27, Tab: 9, Backspace: 8, Delete: 46,
      ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Home: 36, End: 35, Space: 32 };
    const keyCode = special[key] ?? (/^F([1-9]|1[0-2])$/.test(key) ? 111 + Number(key.slice(1))
      : key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0);
    if (!keyCode) throw Error(`Unknown key ${key}`);
    const code = /^[a-z]$/i.test(key) ? `Key${key.toUpperCase()}`
      : /^\d$/.test(key) ? `Digit${key}` : key;
    const params = { key: key === 'Space' ? ' ' : key, code, modifiers,
      windowsVirtualKeyCode: keyCode, nativeVirtualKeyCode: keyCode };
    await send('Input.dispatchKeyEvent', { type: 'keyDown', ...params });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', ...params });
  } else if (action === 'click') {
    const selector = JSON.stringify(required());
    const point = await evaluate(`(() => {
      const elements = [...document.querySelectorAll(${selector})].filter(e => {
        const r = e.getBoundingClientRect(); return r.width && r.height;
      });
      if (elements.length !== 1) throw Error('Expected one visible match, found ' + elements.length);
      elements[0].scrollIntoView({block: 'center'});
      const r = elements[0].getBoundingClientRect();
      return {x: r.x + r.width / 2, y: r.y + r.height / 2};
    })()`);
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point });
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1 });
  } else if (action === 'screenshot') {
    const path = required();
    const { data } = await send('Page.captureScreenshot', { format: 'png' });
    await writeFile(path, Buffer.from(data, 'base64'));
    console.log(path);
  } else throw Error(`Unknown action ${action}; run help`);
} catch (error) {
  console.error(`vscode-ui: ${error.message}`);
  process.exitCode = 1;
} finally {
  socket?.close();
}
