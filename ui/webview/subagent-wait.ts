// The subagent viewer's wait (T355, the user 2026-09-11: a workflow agent's transcript "stuck loading forever"). The
// viewer asks the kernel for the agent's frame once and paints the loader until it lands; a frame that never comes (the
// kernel restarted between the ask and its answer, the relay socket to the agent's host dropped) left the loader up for
// good, with nothing to press. After SUBAGENT_OPEN_WAIT_MS with no frame the pane says so and offers a retry; every open
// viewer asks again when the socket comes back (the reconnected kernel holds no viewer registry for this client).
// The bound: the kernel answers an open synchronously on the socket thread, so a local answer takes the frame's build
// (well under a second for a capped tail) and a relayed one adds the relay's handshake, whose own bound is fifteen
// seconds (REMOTE_CONNECT_MS in federation.ts); past that the frame is not coming on this socket.
export const SUBAGENT_OPEN_WAIT_MS = 15000;

export function subagentStallText(seconds: number = SUBAGENT_OPEN_WAIT_MS / 1000): string {
  return "The agent's transcript has not arrived after " + seconds + " seconds. The kernel may have restarted, or the " +
         "connection to its host dropped.";
}

/** Whether a viewer whose ask went out `sinceMs` ago with no frame should show the stall instead of the loader. */
export function subagentStalled(loaded: boolean, sinceMs: number, waitMs: number = SUBAGENT_OPEN_WAIT_MS): boolean {
  return !loaded && sinceMs >= waitMs;
}
