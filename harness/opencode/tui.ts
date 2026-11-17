import { Plugin } from "@opencode/plugin/tui"
import { Notices } from "./rpc.ts"

/** Shows the gate's user-facing messages as toasts; the server part is index.ts. */
export default Plugin.define({
  id: "papers-please",
  setup: (context) =>
    context.client.rpc(Notices).events.on("notice", (event) => {
      const { sessionID, text } = event.data as { sessionID: string; text: string }
      context.ui.toast.show({ title: "papers-please", message: text, variant: "warning", duration: 15000, sessionID })
    }),
})
