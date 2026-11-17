import { Rpc } from "@opencode/plugin"

/** The server part's event for a gate message meant for the user, not the model; the TUI part shows it as a toast. */
export const Notices = Rpc.define({
  id: "papers-please",
  methods: {},
  events: {
    notice: {
      schema: {
        type: "object",
        properties: { sessionID: { type: "string" }, text: { type: "string" } },
        required: ["sessionID", "text"],
        additionalProperties: false,
      },
    },
  },
})
