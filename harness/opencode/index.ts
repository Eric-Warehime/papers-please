/**
 * OpenCode server plugin: runs the same gate as harness/claude-code/hooks.json does in Claude Code, through gate/run.sh.
 * A session's first prompt, each prompt, each edit/write and the moment a session would go idle (OpenCode's Stop) each
 * run `gate.py`. A blocked Stop becomes a synthetic message that keeps the session working; any other message is
 * for the user, so it goes to the TUI part (tui.ts) as a toast, since input admitted while a session runs or idles
 * is always read by the model.
 */
import { Plugin } from "@opencode/plugin"
import { Notices } from "./rpc.ts"
import { spawn } from "node:child_process"
import { readFile } from "node:fs/promises"
import path from "node:path"
import { fileURLToPath } from "node:url"

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..")
const SOURCE = "papers-please"

type Reply = { code: number; message: string; context: string }

/** Runs `gate.py <event>` with a Claude Code shaped hook event. Exit 2 blocks, with the feedback as `message`;
 * otherwise `message` is for the user and `context` for the model. */
function gate(cwd: string, event: string, input: Record<string, unknown>): Promise<Reply> {
  return new Promise((resolve) => {
    const child = spawn("sh", [path.join(ROOT, "gate", "run.sh"), event], { cwd })
    let out = ""
    let err = ""
    child.stdout.on("data", (chunk) => (out += chunk))
    child.stderr.on("data", (chunk) => (err += chunk))
    child.on("error", (error) => resolve({ code: 1, message: `papers-please could not run: ${error.message}`, context: "" }))
    child.on("close", (code) => {
      if (code === 2) return resolve({ code, message: err.trim(), context: "" })
      let parsed: { systemMessage?: string; hookSpecificOutput?: { additionalContext?: string } } = {}
      try {
        if (code === 0) parsed = out.trim() ? JSON.parse(out) : {}
      } catch {
        code = 1
      }
      // A crashed gate says so rather than silently gating nothing.
      const message = code === 0 ? (parsed.systemMessage ?? "") : `papers-please failed:\n${(err || out).trim()}`
      resolve({ code: code ?? 1, message, context: parsed.hookSpecificOutput?.additionalContext ?? "" })
    })
    child.stdin.end(JSON.stringify({ ...input, cwd, hook_event_name: event }))
  })
}

export default Plugin.define({
  id: SOURCE,
  async setup(ctx) {
    const cwd = ctx.location.directory
    const parents = new Map<string, Promise<boolean>>()
    /** Whether the session is a subagent's: like Claude Code's Stop hook, the gate judges only top-level sessions. */
    const child = (sessionID: string) => {
      if (!parents.has(sessionID))
        parents.set(sessionID, ctx.session.get({ sessionID }).then((s) => Boolean(s.parentID)))
      return parents.get(sessionID)!
    }
    const notices = await ctx.rpc.register(Notices, {})
    /** Delivers a gate message: a block to the model, in the session's timeline, so it keeps working; the rest to the user. */
    const notice = (sessionID: string, text: string, block: boolean) =>
      block
        ? ctx.session.synthetic({ sessionID, text, description: SOURCE, metadata: { source: SOURCE } })
        : notices.events.emit("notice", { sessionID, text })

    // The session check runs at a session's first prompt, as Claude Code's SessionStart does at startup. The prompt
    // check records the turn's starting state; the context it returns was already shown as a notice at the last Stop.
    const started = new Set<string>()
    await ctx.session.hook("prompt", async (event) => {
      const { sessionID } = event
      if (await child(sessionID)) return
      if (!started.has(sessionID)) {
        started.add(sessionID)
        const { message } = await gate(cwd, "session", { session_id: sessionID })
        if (message) await notice(sessionID, message, false)
      }
      await gate(cwd, "prompt", { session_id: sessionID, prompt: event.prompt.text })
    })

    await ctx.tool.hook("execute.after", async (event) => {
      if (event.status !== "completed" || (event.tool !== "edit" && event.tool !== "write")) return
      const file = (event.input as { path?: string }).path
      if (!file) return
      const { message, context } = await gate(cwd, "edit", {
        session_id: event.sessionID,
        tool_input: { file_path: path.resolve(cwd, file) },
      })
      const text = message || context
      if (!text) return
      // The model reads `content`; `output` only stands in when `content` is empty.
      const { content } = event.result
      const parts = typeof content === "string" ? [{ type: "text" as const, text: content }] : [...(content ?? [])]
      event.result = { ...event.result, content: [...parts, { type: "text", text }] }
    })

    await ctx.session.hook("experimental.idle", async (event) => {
      if (await child(event.sessionID)) return
      const messages = await ctx.session.context({ sessionID: event.sessionID })
      const { code, message } = await gate(cwd, "stop", { session_id: event.sessionID, messages })
      if (message) await notice(event.sessionID, message, code === 2)
    })

    const skill = (await readFile(path.join(ROOT, "skills", "doctor", "SKILL.md"), "utf8")).replace(/^---[\s\S]*?---\n/, "")
    await ctx.command.transform((editor) =>
      editor.add({
        name: `${SOURCE}:doctor`,
        description: "Check that the tools papers-please needs work, and install the missing ones",
        execute: async ({ sessionID, prompt, delivery }) => {
          const text = skill.replaceAll("${CLAUDE_PLUGIN_ROOT}", ROOT).replaceAll("$ARGUMENTS", prompt.text)
          await ctx.session.prompt({ ...prompt, sessionID, text, delivery })
        },
      }),
    )
  },
})
