// TAB GROUPS ARE TAGS (the user 2026-09-04). The chat tab strip renders one section per tag, in the
// user's tag order, each tab under EVERY tag it carries (T264b, the user 2026-09-08: tags are
// equivalent, none takes precedence — a session under N tags has a copy in N sections; the earlier
// "home tag" rule that placed it under its first holder alone is retired). Sessions in no tag trail
// in an unlabeled section of their own. No new store and no new session field: the kernel's views
// blob (tags, members, tagOrder) is the whole model, the headers are the very tags the Tags flyout
// edits, and reordering the groups IS reordering tagOrder — the kernel-persisted union order the
// timeline's tag-pill drag writes too, so the two surfaces cannot disagree.
//
// Per-browser state, this viewer's like romp:vieworder: whether the strip sections at all (ON by
// default whenever some tag holds a visible tab; the chat tag-lens menu's "Group tabs by tag" turns
// it off), which sections are folded, and which members show through their section's fold (the
// tab menu's "Show when folded"). `archived` starts folded — that tag exists to put sessions away.
// Notification is view-order.ts's two-path idiom: localStorage reaches other panes (the storage
// event), a same-window CustomEvent reaches the writer. Pure and DOM-free (the tab-order.ts
// pattern) so the rule executes in node tests; render.ts paints it.
import { SessionViews, TagUnion, viewTags, viewTagUnion } from "./session-views";
import { hostOf } from "./host-prefix";

export const TABGROUPS_KEY = "romp:tabgroups";
export const TABGROUPS_EVENT = "romp-tabgroups";
/** sections that start folded until the user opens them (remembered per browser once toggled) */
export const DEFAULT_COLLAPSED: ReadonlySet<string> = new Set(["archived"]);

/** One strip section: a tag's name + color and the visible tabs it holds, plus `localId` — the
 *  local tag's stored id when the union has one, null for a union only remote hosts' tags make —
 *  which its pins are matched against beside its name (isPinned). name null = the trailing untagged
 *  section (unlabeled by the user's ruling — a separator, not a header; localId null). */
export interface TabSection { name: string | null; localId: string | null; color: string; ids: string[] }

/** A section as a pin is matched and written against it: its name and its local tag's id. The plan's
 *  TabSection is one; sectionRef builds one from a union (the menu row's tag). */
export type SectionRef = Pick<TabSection, "name" | "localId">;

/** A member kept visible under its folded section — the tab menu's "Show when folded" (the user
 *  2026-09-06). ONE entry per (tab, section): `sid` the tab, `name` the section's displayed name when
 *  the pin was made, `id` the local tag's id when the section had one (a remote host's tag id is never
 *  stored: it is the host's, and a section a remote tag makes is addressed by its name). A view
 *  preference like the fold itself: per browser.
 *
 *  Why both: a section is what the user sees, and it is made by whichever tags share the name — the
 *  local one, remote hosts' ones, or both together — a set that changes under the pin with no gesture
 *  on the tab (a host attaches or detaches, a same-named tag appears on the other side, the local one
 *  is renamed or deleted). Three rounds of the branch's review (2026-09-06) found the same defect in
 *  every scheme that derived ONE key from the holders at click time: the tab folded away when the set
 *  later changed. Storing the name AND the local id, and matching under either, follows the section
 *  through every such change; the id follows the local tag's rename, the name follows the remote
 *  tag's hold. */
export interface PinnedRef { sid: string; name: string; id?: string }

export interface TabGroupsState {
  on: boolean;          // the sectioned strip (default true)
  collapsed: string[];  // sections the user folded
  expanded: string[];   // default-folded sections the user opened
  pinned: PinnedRef[];  // members shown under their folded section
  /** the name each renamed tag's pins were last carried to, by tag id — followTagRenames' once-per-
   *  browser memory; absent until a rename was followed */
  followed?: Record<string, string>;
  /** the write seq of each followed tag's STORE when its memory was stamped, by tag id — the local
   *  blob's `seq` for a local tag, the owning host's own for a remote one (the kernel carries it per
   *  remoteTag row) — the evidence the memory rests on, which a blob older than it stands down against
   *  (followTagRenames); absent for an entry stamped from a blob that carried none (a kernel from before
   *  the stamp, or a store from before it), which no blob is older than */
  followedSeq?: Record<string, number>;
}

/** The section a union makes, as pins are matched and written against it. */
export function sectionRef(u: TagUnion): SectionRef {
  return { name: u.name, localId: u.localId };
}

/** Section the visible ids: one section per TAG, holding EVERY visible member it carries (T264b, the
 *  user 2026-09-08: tags are equivalent — no tag takes precedence, so a session under N tags appears
 *  in N groups; the "home tag" rule that placed it under its first holder is retired). Sections in
 *  UNION order (tagOrder governs — which is why reordering tags is first-class), tabs inside keep
 *  their strip order, the untagged trail — the ids in NO tag — last. A tag holding no visible id
 *  yields no section. */
export function sectionTabs(visibleIds: readonly string[], unions: readonly TagUnion[]): TabSection[] {
  const out: TabSection[] = [];
  const tagged = new Set<string>();
  for (const u of unions) {
    const ids = visibleIds.filter((id) => u.members.includes(id));
    if (!ids.length) continue;
    for (const id of ids) tagged.add(id);
    out.push({ name: u.name, localId: u.localId, color: u.color || "", ids });
  }
  const loose = visibleIds.filter((id) => !tagged.has(id));
  if (loose.length) out.push({ name: null, localId: null, color: "", ids: loose });
  return out;
}

/** Does any tag hold a visible tab? Sectioning is on by default exactly then — an untagged world
 *  renders the flat strip it always had. */
export function anySectioned(visibleIds: readonly string[], unions: readonly TagUnion[]): boolean {
  return visibleIds.some((id) => unions.some((u) => u.members.includes(id)));
}

const fresh = (): TabGroupsState => ({ on: true, collapsed: [], expanded: [], pinned: [] });

/** A stored blob; anything malformed reads as the default rather than throwing (view-order's rule:
 *  a corrupt entry may cost you a preference, never the dashboard). `unions` — the current tag
 *  unions — serve the one migration: an entry in the branch's earlier shape, `{tag, sid}` with `tag`
 *  a local tag's id or a union's name, becomes `{sid, id: tag, name: <that union's name>}` when `tag`
 *  is a current local id, else `{sid, name: tag}` (a name, or a deleted tag's id — the prune drops the
 *  latter on the next pin write, as before). Readers that write back pass the unions they have, so a
 *  migration is faithful; a read before the first views frame sees none and would keep such an
 *  entry's id as a name — nothing writes the store before a sectioned strip has rendered, and that
 *  needs a frame. */
export function parseTabGroups(raw: string | null | undefined, unions: readonly TagUnion[] = []): TabGroupsState {
  if (!raw) return fresh();
  try {
    const o = JSON.parse(raw);
    if (!o || typeof o !== "object" || Array.isArray(o)) return fresh();
    const strs = (xs: unknown) => (Array.isArray(xs) ? xs.filter((x): x is string => typeof x === "string") : []);
    const pins = (xs: unknown): PinnedRef[] => {
      if (!Array.isArray(xs)) return [];
      const out: PinnedRef[] = [];
      for (const x of xs) {
        if (!x || typeof x !== "object" || typeof x.sid !== "string") continue;
        if (typeof x.name === "string") { out.push(typeof x.id === "string" ? { sid: x.sid, name: x.name, id: x.id } : { sid: x.sid, name: x.name }); continue; }
        if (typeof x.tag !== "string") continue;
        const u = unions.find((g) => g.localId === x.tag);
        out.push(u ? { sid: x.sid, name: u.name, id: x.tag } : { sid: x.sid, name: x.tag });
      }
      return out;
    };
    const names = (x: unknown): Record<string, string> | undefined => {
      if (!x || typeof x !== "object" || Array.isArray(x)) return undefined;
      const out: Record<string, string> = {};
      for (const [k, v] of Object.entries(x as Record<string, unknown>)) if (typeof v === "string") out[k] = v;
      return Object.keys(out).length ? out : undefined;
    };
    const followed = names(o.followed);
    // the stamps ride for remembered tags only, and only positive ones (0 and junk read as none: no blob is older)
    const seqs = (x: unknown): Record<string, number> | undefined => {
      if (!followed || !x || typeof x !== "object" || Array.isArray(x)) return undefined;
      const out: Record<string, number> = {};
      for (const [k, v] of Object.entries(x as Record<string, unknown>)) if (k in followed && typeof v === "number" && Number.isFinite(v) && v > 0) out[k] = v;
      return Object.keys(out).length ? out : undefined;
    };
    const followedSeq = seqs(o.followedSeq);
    return { on: o.on !== false, collapsed: strs(o.collapsed), expanded: strs(o.expanded), pinned: pins(o.pinned),
             ...(followed ? { followed } : {}), ...(followedSeq ? { followedSeq } : {}) };
  } catch {
    return fresh();
  }
}

export function readTabGroups(unions: readonly TagUnion[] = []): TabGroupsState {
  try {
    return parseTabGroups(localStorage.getItem(TABGROUPS_KEY), unions);
  } catch {
    return fresh();   // private mode / blocked storage → the defaults, every time
  }
}

/** Persist and tell every pane — `storage` fires only in OTHER same-origin contexts, so the writing
 *  window gets the same news through a CustomEvent (one notification path, two deliveries). */
export function writeTabGroups(st: TabGroupsState): void {
  try {
    const blob: Record<string, unknown> = { on: st.on, collapsed: st.collapsed, expanded: st.expanded, pinned: st.pinned };
    if (st.followed && Object.keys(st.followed).length) blob.followed = st.followed;
    if (st.followedSeq && Object.keys(st.followedSeq).length) blob.followedSeq = st.followedSeq;
    localStorage.setItem(TABGROUPS_KEY, JSON.stringify(blob));
  } catch {
    /* quota / private mode → this preference just doesn't outlive the page */
  }
  try {
    window.dispatchEvent(new CustomEvent(TABGROUPS_EVENT));
  } catch {
    /* no window (node test) */
  }
}

/** Folded? The user's explicit fold or open wins; otherwise the default set decides. */
export function isSectionCollapsed(st: TabGroupsState, name: string): boolean {
  if (st.collapsed.includes(name)) return true;
  if (st.expanded.includes(name)) return false;
  return DEFAULT_COLLAPSED.has(name);
}

/** Set a section's fold state EXPLICITLY. The header's click passes the state it RENDERED, not the
 *  stored one: the active tab's section renders open whatever the store says, and toggling the
 *  stored bit there inverted the click — "fold" on a stored-folded, rendered-open `archived` stored
 *  it OPEN for good, with nothing visible changing. Minimal storage: a name is listed only where it
 *  differs from the default set. */
export function setSectionCollapsed(st: TabGroupsState, name: string, folded: boolean): TabGroupsState {
  const collapsed = st.collapsed.filter((n) => n !== name);
  const expanded = st.expanded.filter((n) => n !== name);
  if (folded) { if (!DEFAULT_COLLAPSED.has(name)) collapsed.push(name); }
  else if (DEFAULT_COLLAPSED.has(name)) expanded.push(name);
  return { ...st, collapsed, expanded };
}

export function toggleSectionCollapsed(st: TabGroupsState, name: string): TabGroupsState {
  return setSectionCollapsed(st, name, !isSectionCollapsed(st, name));
}

/** Does an entry name this section? Its name, OR its id when the section has a local tag. The
 *  untagged trail has no fold, so nothing names it. */
function pinNames(sec: SectionRef, p: PinnedRef): boolean {
  return sec.name !== null && (p.name === sec.name || (p.id !== undefined && p.id === sec.localId));
}

/** The entry a pin on `sid` in this section stores: the section's name, and its local tag's id when
 *  it has one. */
function pinEntry(sec: SectionRef, sid: string): PinnedRef {
  const name = sec.name ?? "";
  return sec.localId ? { sid, name, id: sec.localId } : { sid, name };
}

/** Is this member kept visible under its folded section? An entry for the sid naming the section by
 *  its name or its local id — see PinnedRef. */
export function isPinned(st: TabGroupsState, sec: SectionRef, sid: string): boolean {
  return st.pinned.some((p) => p.sid === sid && pinNames(sec, p));
}

/** Set a member's pin EXPLICITLY (the fold's own idiom: the menu row passes the state it rendered).
 *  A write is PER SECTION: on adds, or replaces, the tab's entry for the section that is its home now
 *  (the entries naming it by name or by id collapse into one), and off removes exactly those. Entries
 *  the tab holds for OTHER sections stand either way — the menu row is offered in the home section
 *  alone, its copy speaks of that section ("while <name> is folded"), and a pin the user set under a
 *  section that a drag or a rename later makes the home again is theirs until they clear it there
 *  (an on that replaced every entry silently dropped a pin set under another section, and the row's
 *  copy had promised a per-section preference). */
export function setPinned(st: TabGroupsState, sec: SectionRef, sid: string, on: boolean): TabGroupsState {
  const pinned = st.pinned.filter((p) => !(p.sid === sid && pinNames(sec, p)));
  if (on && sec.name !== null) pinned.push(pinEntry(sec, sid));
  return { ...st, pinned };
}

export function togglePinned(st: TabGroupsState, sec: SectionRef, sid: string): TabGroupsState {
  return setPinned(st, sec, sid, !isPinned(st, sec, sid));
}

/** Drop the pins nothing can render any more, judged PER ENTRY — and only where the entry's session
 *  CAN be judged from this page. An entry is JUDGED when its session's fate is knowable here: the sid is
 *  a tab the strip knows, or it is local (the page's own kernel lists every local session), or its host
 *  is in `hosts` — the remote hosts attached with the tunnel up whose tab list has reached this pane
 *  (reachableFrom). Judged, it stands only while the session is a known tab and some union the entry
 *  names (by its name, or by its id as a local id) holds the session — so the store does not keep an
 *  entry for every tag ever deleted, session ever closed, or member moved out. NOT judged — a sid whose
 *  host is detached, down, or attached and up with its tab list still on its way to this pane — it
 *  stands untouched: the host's sessions left the strip with the host (a detach dismisses every one; a
 *  page loaded during an outage never had them) or have not reached it yet, and its tags left the blob
 *  or stand cached, none of which is a session's end. The entries wait for the host's tabs, and the
 *  next pin write after that judges them for real
 *  (a pin click while a host was detached dropped every pin on its sessions, and they folded away on
 *  the reattach with no gesture on them; the same click in the seconds between a host's attach or a
 *  page load and its tab list's arrival did the same).
 *  On the WRITE path only (the pin row's click): a prune there moves nothing on screen, where a prune
 *  per render could act on a transient frame (a views blob mid-write, a reattached host's tags one
 *  supervisor pass behind its tabs) and put a tab away with no gesture. Returns `st` itself when
 *  nothing is dropped.
 *
 *  THE LIMITS: a LOCAL tab pinned under a section that only a remote host's tag made (the host tagged
 *  one of ours) is judged on that host's detach — the sid is local, and the entry names the section by
 *  name alone, a remote tag's id and so its host never being stored — and dropped, since no union of
 *  the name holds it; the user sets the pin again after the reattach. And a reattached host's tabs and
 *  tags reach this pane by two roads: the tabs over the relay, within a /tunnels poll (4 s) of the row
 *  coming up and the socket's connect; the tags on the LOCAL kernel's views blob, which the tunnel
 *  supervisor's pass that marks the host up reads and caches, and which the pusher ships on its next
 *  tabOrder frame — the kernel wakes the pusher when a host's cached read changes
 *  (`_cache_remote_views`), so that is the next cycle: sub-second on an idle box, the end of the cycle
 *  in flight on a loaded one (20-40 s). So the tabs can lead the tags by up to a pusher cycle, and by
 *  a supervisor pass (15 s) more when the kernel's first read of the host's /views fails while its
 *  /sessions probe answers: a pin write in that window sees the host's sessions as known tabs that no
 *  union of the pinned name holds, and drops its remote-only pins (a mixed pin whose local tag holds the
 *  member stands); the user sets them again. */
export function prunePinned(st: TabGroupsState, unions: readonly TagUnion[], knownIds: ReadonlySet<string>, hosts: ReadonlySet<string>): TabGroupsState {
  const judged = (sid: string) => { const h = hostOf(sid); return knownIds.has(sid) || h === "" || hosts.has(h); };
  const pinned = st.pinned.filter((p) => !judged(p.sid) || (knownIds.has(p.sid)
    && unions.some((u) => (u.name === p.name || (p.id !== undefined && u.localId === p.id)) && u.members.includes(p.sid))));
  return pinned.length === st.pinned.length ? st : { ...st, pinned };
}

/** The lists the federation router publishes on `window.__rompFed` (federation.ts start()) that the pin
 *  prune reads; any may be absent — a single-kernel page runs no router, an older one lists less. */
export interface RouterHosts { hosts?: () => string[]; down?: () => string[]; pending?: () => string[] }

/** The remote hosts whose sessions this pane CAN know right now — prunePinned's `hosts`, the "this entry
 *  can be judged": attached (`hosts`), with the tunnel up (not `down`), and with this pane's own copy of
 *  their tab list in hand (not `pending` — the router's set of attached hosts whose tabOrder has not
 *  reached the pane: the seconds after a page load or an attach, or a stretch where the relay socket
 *  never opens while the kernel's tunnel probe answers). A host in that set is up and lists sessions,
 *  and none of them is a known tab here yet — judged, every pin on its sessions would drop. A
 *  detached host's sessions left the strip with it, and a down host's never
 *  arrived on a page loaded during the outage; none of this is a session's end, so their pins stand
 *  until the host's tabs are here. No router → no remote host: every sid is local there, and local sids
 *  are always judged. */
export function reachableFrom(fed: RouterHosts | null | undefined): Set<string> {
  const list = (k: "hosts" | "down" | "pending"): string[] => (fed && typeof fed[k] === "function" ? fed[k]!() : []) || [];
  const down = new Set(list("down")), pending = new Set(list("pending"));
  return new Set(list("hosts").filter((h) => !down.has(h) && !pending.has(h)));
}

/** A tag that kept its id and changed its name between two views blobs: `local` for one of this
 *  kernel's tags (the id a pin may carry), false for a remote host's; `members` what it holds now. */
export interface TagRename { id: string; from: string; to: string; local: boolean; members: readonly string[] }

/** The (tag id → name) map of a blob's local and remote tags — what a rename is a change of, and what a
 *  blob that carries no rename leaves as it was. (A blob's hosts are in it too: a remote id is its host's.) */
export function tagNames(v: SessionViews | null | undefined): Map<string, string> {
  const out = new Map<string, string>();
  if (!v) return out;
  for (const t of viewTags(v)) out.set(t.id, t.name || "tag");
  for (const t of v.remoteTags || []) out.set(t.id, t.name || "tag");
  return out;
}

/** Do two blobs name the same tags by the same names — the same ids, local and remote, each under the
 *  same name? True is "no news about tag names" (followAdoption). */
export function sameTagNames(a: SessionViews | null | undefined, b: SessionViews | null | undefined): boolean {
  const x = tagNames(a), y = tagNames(b);
  if (x.size !== y.size) return false;
  for (const [id, name] of x) if (y.get(id) !== name) return false;
  return true;
}

/** The tags `next` renames relative to `prev` — the previously adopted blob, so a client watching the
 *  stream sees every rename, local or a remote host's, as the frame that carries it arrives. No
 *  previous blob (the page's first frame) → none: a rename that happened while no client watched is
 *  matched by id where an id was stored (isPinned), and is missed for a remote-only pin, which has
 *  none (followTagRenames states the limit). */
export function tagRenames(prev: SessionViews | null | undefined, next: SessionViews | null | undefined): TagRename[] {
  if (!prev || !next) return [];
  const before = tagNames(prev);
  const out: TagRename[] = [];
  const see = (id: string, name: string | undefined, local: boolean, members: readonly string[] | undefined) => {
    const from = before.get(id), to = name || "tag";
    if (from !== undefined && from !== to) out.push({ id, from, to, local, members: members || [] });
  };
  for (const t of viewTags(next)) see(t.id, t.name, true, t.members);
  for (const t of next.remoteTags || []) see(t.id, t.name, false, t.members);
  return out;
}

/** The write seq of a tag's STORE as a blob carries it, by tag id — the blob's own `seq` for a local tag,
 *  the owning host's own off its remoteTags rows for a remote one (the kernel puts it on each row) — or
 *  null where the blob carries none for that store. The evidence order followTagRenames and
 *  followAdoption read. */
function storeSeqs(unions: readonly TagUnion[], views: SessionViews | null | undefined): (id: string) => number | null {
  const localSeq = typeof views?.seq === "number" && Number.isFinite(views.seq) ? views.seq : null;
  const hostSeq = new Map<string, number>();
  for (const u of unions) for (const t of u.remotes) {
    const h = t.host || hostOf(t.id);
    if (h !== "" && typeof t.seq === "number" && Number.isFinite(t.seq)) hostSeq.set(h, t.seq);
  }
  return (id) => { const h = hostOf(id); return h === "" ? localSeq : (hostSeq.get(h) ?? null); };
}

/** Carry the pins across the tags a blob renamed (tagRenames), so a pinned tab stays pinned to its
 *  group through the group's rename — the name a pin stores is the section's displayed name, and a
 *  rename changes it. An entry FOLLOWS a rename when it carries the renamed tag's id, or carries the
 *  old name and the renamed tag holds its tab: for an entry with no id (a remote-only pin, made before
 *  a local tag of the name existed), any such tag's rename; for an entry with a local id, a REMOTE
 *  tag's — its own tag's rename reaches it by id, and two local tags never share a name. The other
 *  same-named tags' pins are not this tag's to move. EVERY matching rename is followed, one entry per:
 *  the new name, with the tag's id when the tag is local — so the next rename finds it by id even from
 *  a client with no previous blob. Where a rename SPLITS the section — a same-named tag on the other
 *  side still holds the tab, so the tab shows under both halves, in tagOrder (the kernel leaves
 *  tagOrder alone on a rename, so an old name once dragged into place keeps it and the renamed tag
 *  falls behind) — the half the tab did not move to keeps its entry beside the new one, whichever
 *  side renamed: a local rename keeps the old-name half while the remote tag holds the
 *  tab, a remote rename adds its new-name half while the local tag holds the tab under the old. The
 *  tab is pinned in both halves, and the prune drops the half that stops holding it. `unions` are the
 *  NEXT blob's. Exact duplicates collapse.
 *
 *  ONCE PER BROWSER: the store remembers, by tag id, the name each renamed tag's pins were last carried
 *  to (`followed`), and a rename to that name is already followed. Every pane of the browser computes
 *  renames against ITS OWN held blob, so a pane adopting a frame late (a background tab whose socket
 *  redialed, a stale base coalescing several frames) computes the rename the first pane followed and,
 *  following it again, would undo what the user did in between — a pin turned off after the split —
 *  or split a pin made after the rename; the rename was the event, and it was acted on. The memory
 *  is the RENAME's, not a frame's: a remote host's rename rides the
 *  local blob (remoteTags, the kernel's cached read of the host) with no change to the blob's write
 *  seq, so no seq could name it; and a tag renamed back and then forth again is followed each time,
 *  each being to a name the memory does not hold for it. Every rename the frame carries is remembered,
 *  matched or not, and the memory is checked against EVERY adopted blob, renames or none (adoptBase
 *  runs this on each), and kept to what the blob confirms — PER HOST: an entry stands while the blob
 *  names its tag by the remembered name (the late pane's case: the tag is where its pins were carried),
 *  or while it is a remote host's (`host:tid`) and the blob carries none of that host's tags — a
 *  detach pops the host's cached read (a DOWN host's stays), and a reattach's tabs can run ahead of it
 *  (prunePinned's limits) — since the blob cannot say whether the tag is gone or the host is, and a
 *  rename followed in that window would otherwise erase the memory of the host's renames, for a stale
 *  pane to re-apply one after the reattach. A deleted tag's entry
 *  goes: a local id the store lacks, or a remote id its host lists without. And a tag the blob names by
 *  ANOTHER name was renamed on while no client watched (the page closed; the host detached; a pane's
 *  socket dead across two renames), and its entry is a rename this browser OWES: the memory says where
 *  the tag's pins were left, the blob where the tag is now, so the pins under the remembered name are
 *  carried to the blob's name in this adoption, as a watched rename's are — the tag's own members
 *  deciding (`locals` / `remotes` on the union: the merge would reach a same-named tag's tabs) — and the
 *  memory is re-stamped. Kept as it was, the memory read the tag's next rename to the remembered name
 *  as already followed: a remote tag renamed web → api (followed), back to web while its host was
 *  detached, and to api again after the user pinned the tab under web kept the pin under web, and the
 *  tab folded away with no gesture on it. Dropped without the carry, it left the pin where
 *  the late pane could not find it: a pane whose held blob predated two renames (web → api, watched by
 *  another pane, which carried the pin to api; then api → ops) computed the coalesced web → ops,
 *  matched nothing under web, and stamped ops — and the watching pane's api → ops then read as already
 *  followed, the pin still under api and the tab folded away. The frame's own renames
 *  re-stamp the memory after the check (a rename the frame itself carries from the remembered name is
 *  followed once), so a watched rename away from the remembered name is remembered under its new one.
 *  Returns `st` itself when every rename is already followed, or stood down, and the memory stands —
 *  the late pane writes nothing and notifies no one; a stale entry's drop alone is a write, pins
 *  untouched.
 *
 *  THE EVIDENCE ORDER: each memory entry is stamped with the write seq of its tag's STORE as
 *  the adopted blob carried it (`followedSeq`) — the blob's own `seq` for a local tag; for a remote
 *  host's tag the host's own, which the kernel carries on each remoteTag row (a remote rename rides the
 *  local blob with no change to the local seq, so the host's is the seq that orders it) — and a blob
 *  whose seq for that store is OLDER than the stamp stands down on the tag: the entry is kept as it is,
 *  the blob's other name for the tag is not a rename owed, a rename the frame itself carries for the tag
 *  is not followed, and nothing is re-stamped, because the memory was written from newer evidence than
 *  the blob is (the writer whose evidence predates the diary stands down). At the stamp's seq or past
 *  it the blob proceeds. Before this, a pane adopting an intermediate frame late — held V0 (web),
 *  adopting V1 (api) after another pane had carried the pin on V2 (ops) — read ops against api as the
 *  rename it owed, carried the pin to api, and its own V2 frame carried it back: a flap on no new
 *  information; with the stamp, its V1 is older than the memory and moves nothing. `views` is the
 *  adopted blob, for the local seq; a seq the blob does not carry (no `views`; a kernel or host from
 *  before the stamp) orders nothing — such a tag is never stood down, its stamp is not stored, and the
 *  check runs as it did. The re-adoption of the very blob a pane holds is a different case, and
 *  followAdoption's name check answers it before this runs, seq or none.
 *
 *  THE LIMITS: a remote-only pin has no id, so a rename of the remote tag that happens while no client
 *  of this browser is watching (the page closed, the blob's first frame after it) is followed only
 *  through the memory — this browser having followed an earlier rename of the tag, which ties the
 *  pin's name to the tag's id. With no memory of the tag, the entry stays under the old name, where it
 *  matches nothing until the user pins the tab again; the pin set again follows the tag's next rename,
 *  and from then on its unwatched ones too. A local tag's pin carries the id and has no such gap. A
 *  memory entry without a stamp — written before the stamp, or from a blob that carried no seq — stands
 *  no blob down, so the late-intermediate flap above remains possible for that tag until a followed
 *  rename stamps it. A store restored to an OLDER copy (a kernel restarted over a restored views
 *  file serves it under the old seq) is stood down for its tags until its next write, whose seq —
 *  time-ordered on every kernel — passes the stamp. And two name states can share ONE seq on one path:
 *  the timeline's Obsidian (Electron) branch writes the views file itself under the seq it holds, and the
 *  kernel's reader leaves an equal seq alone, so two renames made there in a row reach every client under
 *  one seq (the kernel's own tag door bumps the seq on every write, so no other surface does this). A pane
 *  processing the first of them late, after another pane has followed the second, is not stood down —
 *  the seq is at the stamp, not below it — and moves the pin once, its own next frame carrying it back:
 *  one round, a remote-only pin folded away between that pane's two adoptions. */
export function followTagRenames(st: TabGroupsState, renames: readonly TagRename[], unions: readonly TagUnion[], views?: SessionViews | null): TabGroupsState {
  // the memory, checked against the blob FIRST (the doc above): an entry stands while the blob is older
  // than its stamp for the tag's store, while the blob names its tag by the remembered name, or while it
  // is a remote host's and the blob carries none of that host's tags; a deleted tag's goes; and one the
  // blob names OTHERWISE is a rename this browser owes — from the remembered name to the blob's, over the
  // tag's own members — followed with the frame's, unless the frame itself carries that rename (then it
  // is followed once, as the frame's)
  const byId = new Map<string, TagUnion>();
  for (const u of unions) for (const id of u.ids) byId.set(id, u);
  const present = new Set([...byId.keys()].map(hostOf).filter((h) => h !== ""));
  const storeSeq = storeSeqs(unions, views);   // the stores' seqs as this blob carries them
  const stamp = (id: string): number => st.followedSeq?.[id] ?? 0;
  const older = (id: string): boolean => { const s = storeSeq(id); return s !== null && s < stamp(id); };
  const followed: Record<string, string> = {};
  const seqs: Record<string, number> = {};
  const owed: TagRename[] = [];
  let stale = false;
  for (const [id, name] of Object.entries(st.followed || {})) {
    const u = byId.get(id), h = hostOf(id);
    if (older(id) || u?.name === name || (!u && h !== "" && !present.has(h))) {
      followed[id] = name;
      if (stamp(id) > 0) seqs[id] = stamp(id);
      continue;
    }
    stale = true;
    if (u && !renames.some((r) => r.id === id && r.from === name)) {
      const lt = u.locals.find((t) => t.id === id), t = lt || u.remotes.find((x) => x.id === id);
      owed.push({ id, from: name, to: u.name, local: lt !== undefined, members: t?.members || [] });
    }
  }
  const fresh = [...renames.filter((r) => followed[r.id] !== r.to && !older(r.id)), ...owed];
  // the memory as it stands after this check, on `base`: the stamps ride only while some entry has one
  const remembered = (base: TabGroupsState): TabGroupsState => {
    const out: TabGroupsState = { ...base, followed };
    delete out.followedSeq;
    if (Object.keys(seqs).length) out.followedSeq = seqs;
    return out;
  };
  if (!fresh.length) return stale ? remembered(st) : st;
  const matches = (p: PinnedRef, x: TagRename) => (p.id !== undefined && x.id === p.id)
    || ((p.id === undefined || !x.local) && x.from === p.name && x.members.includes(p.sid));
  const out: PinnedRef[] = [];
  const seen = new Set<string>();
  const put = (p: PinnedRef) => { const k = `${p.sid} ${p.name} ${p.id ?? ""}`; if (!seen.has(k)) { seen.add(k); out.push(p); } };
  for (const p of st.pinned) {
    const rs = fresh.filter((x) => matches(p, x));
    if (!rs.length) { put(p); continue; }
    // the entry MOVES with its own tag's rename (by id) or, id-less, with any it matches; a remote rename
    // matched by name against a local-id entry adds the remote half and leaves the entry, whose local
    // tag still holds the tab under its own name
    if (p.id !== undefined && !rs.some((r) => r.id === p.id)) put(p);
    for (const r of rs) put(r.local ? { sid: p.sid, name: r.to, id: r.id } : { sid: p.sid, name: r.to });
    for (const from of new Set(rs.map((r) => r.from))) {
      const rest = unions.find((u) => u.name === from && u.members.includes(p.sid));
      if (rest) put(pinEntry(sectionRef(rest), p.sid));
    }
  }
  for (const r of fresh) {   // the frame's renames re-stamp the checked memory, at the tag's store's seq
    followed[r.id] = r.to;
    const s = storeSeq(r.id);
    if (s !== null && s > 0) seqs[r.id] = s; else delete seqs[r.id];
  }
  return remembered({ ...st, pinned: out });
}

/** Is the blob NEWER than the memory's evidence for some tag it names otherwise — a STAMPED memory entry
 *  whose tag the blob names by another name (or lacks, its host's rows present), with the blob's seq for
 *  that store strictly past the stamp? The memory was moved from a blob this pane has not seen, and the
 *  blob postdates that move: news about names, whatever the held blob said (followAdoption). An unstamped
 *  entry orders nothing, and a blob at or below the stamp is not newer. */
function newerThanMemory(st: TabGroupsState, unions: readonly TagUnion[], views: SessionViews): boolean {
  const storeSeq = storeSeqs(unions, views);
  const nameOf = new Map<string, string>();
  for (const u of unions) for (const id of u.ids) nameOf.set(id, u.name);
  for (const [id, name] of Object.entries(st.followed || {})) {
    const stamp = st.followedSeq?.[id] ?? 0, s = storeSeq(id);
    if (stamp > 0 && s !== null && s > stamp && nameOf.get(id) !== name) return true;
  }
  return false;
}

/** The follow as the views adoption runs it (render.ts adoptBase, the one base-assignment site): from
 *  the held blob `prev` to the adopted `next`. A blob that names every tag as the held blob does — the
 *  same ids, local and remote, each under the same name (sameTagNames) — is NO NEWS about names and
 *  returns `st` itself, UNLESS it is newer than the memory's evidence for a tag it names otherwise
 *  (newerThanMemory), when the check runs on it. Every other blob runs the frame's renames and the
 *  memory check, under the stores' seqs (followTagRenames). `unions` are `next`'s, passed when the
 *  caller has them.
 *
 *  THE SHORTCUT: the memory was checked against those very names when
 *  the held blob was adopted, and what has changed since is the memory, by another pane's hand, from a
 *  blob this pane has not seen. Such a blob is the held one re-emitted — the federation router re-emits
 *  its stored blob on a view-order storage event from any pane, a remote host's push, a `closed` frame or
 *  a host drop, and a pane whose local socket is dead (a background tab's throttled redial, a half-open
 *  socket) re-adopts it each time, an equal seq being admitted — or a frame that changed something else.
 *  Run on it, the check read the fresher pane's stamp (memory api, blob web) as the rename this browser
 *  owed IN REVERSE, carried the pin back over that pane's follow and re-stamped; the fresher pane's next
 *  adoption carried it forward again: the pin moved twice per trigger on no new information, for as long
 *  as the stale pane stayed stale.
 *
 *  ITS EXCEPTION: a blob naming every tag as the held one does can still be NEWER than the
 *  memory. Two panes hold api for a remote tag; one's socket dies; the host renames api → ops and the
 *  other pane carries the pin (memory ops, stamped at the host's seq), then closes; the host renames
 *  ops → api; the first pane redials and adopts that blob. The names are the held ones and the blob is
 *  real news: its seq for the host's store is past the stamp, and the memory names the tag otherwise —
 *  the rename this browser owes, ops → api, which the shortcut alone left uncarried: a remote-only pin
 *  stayed under ops, where nothing matched it, and the tab folded away until the tag's next rename (or
 *  for good, once a pin write judged the orphan). So the shortcut yields when a STAMPED memory entry
 *  disagrees with the blob's name for its tag AND the blob's seq for that store is strictly past the
 *  stamp; the check then runs, and the seqs order it. An unstamped entry keeps the shortcut — no blob is
 *  known newer than it, and a stale pane's re-emitted blob from a host that stamps no seq would otherwise
 *  carry the pin back, the re-adoption flap — and so does a blob at or below the stamp, which the check would
 *  stand down on that tag regardless (rule (b), followTagRenames). */
export function followAdoption(st: TabGroupsState, prev: SessionViews | null | undefined, next: SessionViews,
                               unions: readonly TagUnion[] = viewTagUnion(next)): TabGroupsState {
  if (prev && sameTagNames(prev, next) && !newerThanMemory(st, unions, next)) return st;
  return followTagRenames(st, tagRenames(prev, next), unions, next);
}

/** The words a section header wears — its count, its tooltip and its accessible name — pure so the copy
 *  executes in tests (render.ts paints them). Folded, the count is the HIDDEN members, what the header
 *  stands in for; a pinned member's own tab is on screen and is not counted. When EVERY member is pinned
 *  the fold hides nothing, and a "0" beside two visible tabs read as a broken number (the 2026-09-06
 *  review): the count is then the total, and the title says why nothing is hidden. The chevron stays
 *  truthful either way — the section IS folded, and the click opens it. */
export interface HeadWords { count: string; title: string; label: string }

// The click's clause: a click also shows the section in the pane (the section at a glance, tab-snapshot.ts),
// open or folded, so every title says so, except while the pane already shows the section (`shown`): then the
// clause names the fold alone, or the way back to the transcript when the open section also holds the tab
// being read (`back`). `holdsActive`, the section holds the tab being read, is a phrase in the words, not a
// different action: the section folds like any other, and folded, its header is the tab's stand-in.
export function headWords(name: string, total: number, hidden: number, folded: boolean, holdsActive: boolean, back = false, shown = false): HeadWords {
  const n = (k: number) => `${k} session${k === 1 ? "" : "s"}`;
  const reading = holdsActive ? "; holds the tab you are reading" : "";
  const here = holdsActive ? ", holds the tab you are reading" : "";
  if (!folded) {
    // `back`: the pane is showing this section, the section is open and holds the tab being read: the click
    // puts that transcript back (render.ts show-transcript), so the title says so, not "fold", and the spoken
    // label names the action too (render.ts drops the header's aria-expanded in that state, since the press
    // folds nothing). `shown` without `back`: the pane shows this open section but the tab being read is
    // elsewhere, so the click still folds, and the title does not promise to show what the pane already shows.
    const click = back ? "click to go back to the transcript" : shown ? "click to fold this group" : "click to fold this group and see its sessions at a glance";
    return { count: String(total), label: `${name}, ${n(total)}${here}${back ? "; back to the transcript" : ""}`,
             title: `${name} — ${n(total)}${reading}; ${click}; drag to reorder the groups` };
  }
  // folded and `shown`: the header's click just folded the section and put its sessions in the pane, so the
  // click opens it and the title says that alone
  const open = shown ? "click to open this group" : "click to open and see its sessions at a glance";
  if (hidden === 0) {
    const all = total === 1 ? "its one session is" : `all ${total} sessions are`;
    return { count: String(total), label: `${name}, ${n(total)}, folded, all shown${here}`,
             title: `${name} — folded, but ${all} set to show when folded, so none is hidden${reading}; ${open}` };
  }
  return { count: String(hidden), label: `${name}, ${n(hidden)} folded${here}`, title: `${name} — ${n(hidden)} folded${reading}; ${open}` };
}

/** One strip item: a section header (folded or open; `active` = it holds the active tab; `hidden` =
 *  the member ids a folded header stands in for — its members less the pinned ones, [] when open) or
 *  a tab. The same id may appear as a tab under several headers (T264b: a session under N tags has N
 *  copies); render.ts paints each copy as a full tab of the one session. */
export type StripItem = { head: TabSection; folded: boolean; active: boolean; hidden: string[] } | { id: string };
export interface StripPlan {
  items: StripItem[];
  folded: Set<string>;   // the ids a folded header stands in for — keyboard cycling skips them
  sectioned: boolean;
}

/** The strip PLAN render.ts paints, pure so the rule executes in node tests.
 *  - `phone`: the kernel's phone chat page hides the strip and builds its own session list by scraping
 *    every rendered tab; it has no header to unfold and no switch, so a folded section there made its
 *    sessions unreachable (`archived` starts folded). Sectioning is DESKTOP-ONLY: on the phone layout
 *    the plan is the flat strip, always — every visible id, nothing folded.
 *  - `pending`: a provisional tab (a create in flight) with the tags the request named. It renders
 *    under every one of them from the first paint — the way the kernel's frame will place it — instead
 *    of landing in the untagged trail and jumping when the frame arrives.
 *  - A folded section hides its members EXCEPT the pinned ones (the tab menu's "Show when folded"),
 *    which keep their place under the header in strip order; the header stands in for `hidden` alone
 *    (its count reads those), and only those ids join the `folded` set.
 *  - The ACTIVE tab's section folds like any other. Its header is marked `active` whether open or
 *    folded: folded, the header is the hidden tab's stand-in, so keyboard focus never lands on a hidden
 *    node. render.ts focuses it where it would focus the tab, the arrows step from its position
 *    (neighborOfFolded), and the pane shows the section at a glance (tab-snapshot.ts) instead of a
 *    transcript whose tab is nowhere on screen.
 *  - A session under several tags (T264b) has a copy in each section, and each copy's fold stands on
 *    its own. The header of a holder that SHOWS a copy (open, or folded with the copy pinned through the
 *    fold) is marked `active`; when no copy shows anywhere exactly one holder, the first in tagOrder, is
 *    marked: the header that stands in for the tab. Nothing springs open (the active tab's section folds
 *    like any other, above). A tab joins the `folded` set only when EVERY copy is off the strip; one
 *    copy on screen keeps it in the keyboard order. */
export function planStrip(visibleIds: readonly string[], unions: readonly TagUnion[], st: TabGroupsState,
                          activeId: string | null, phone: boolean,
                          pending?: { id: string; tags: readonly string[] } | null): StripPlan {
  let u = unions;
  if (pending && pending.tags.length && visibleIds.includes(pending.id)) {
    u = unions.map((x) => (pending.tags.includes(x.name) && !x.members.includes(pending.id)
      ? { ...x, members: [...x.members, pending.id] } : x));
  }
  const sectioned = !phone && st.on && anySectioned(visibleIds, u);
  const items: StripItem[] = [];
  const folded = new Set<string>();
  if (!sectioned) {
    for (const id of visibleIds) items.push({ id });
    return { items, folded, sectioned };
  }
  const secs = sectionTabs(visibleIds, u);
  // does this section put a copy of `id` on the strip: open, or folded with the copy pinned through the fold
  const shows = (sec: TabSection, id: string): boolean => sec.name === null || !isSectionCollapsed(st, sec.name) || isPinned(st, sec, id);
  const holders = activeId !== null ? secs.filter((sec) => sec.ids.includes(activeId)) : [];
  const shownSomewhere = activeId !== null && holders.some((sec) => shows(sec, activeId));
  for (const sec of secs) {
    // a holder showing a copy is marked; when NO copy shows anywhere the first holder in tagOrder is the
    // one stand-in (T264b; a single holder is holders[0], so it is marked whatever its fold: folded, its
    // header is the hidden tab's stand-in). No fold is forced open here: the active tab's section folds
    // like any other
    const active = activeId !== null && holders.includes(sec) && (shows(sec, activeId) || (!shownSomewhere && sec === holders[0]));
    const f = sec.name !== null && isSectionCollapsed(st, sec.name);
    const hidden = f ? sec.ids.filter((id) => !isPinned(st, sec, id)) : [];
    items.push({ head: sec, folded: f, active, hidden });
    for (const id of sec.ids) { if (hidden.includes(id)) folded.add(id); else items.push({ id }); }
  }
  // a session under several tags (T264b) has a copy in each: it is folded away — skipped by the
  // keyboard — only when EVERY copy is; one copy on screen keeps it in the order
  for (const it of items) if ("id" in it) folded.delete(it.id);
  return { items, folded, sectioned };
}

/** The section a tab is homed in, from a rendered plan's items: the first header whose ids include it,
 *  or null (a flat strip, or an id the plan does not know). */
export function homeSectionOf(items: readonly StripItem[], id: string): TabSection | null {
  for (const it of items) if ("head" in it && it.head.ids.includes(id)) return it.head;
  return null;
}

/** Where the arrows land when the active tab is folded away: the header holding it is its stand-in, so
 *  the step starts THERE. `dir` +1 is the first tab rendered after that header, -1 the last one before
 *  it, wrapping round the strip. Folded ids are not items, so the walk sees only tabs on screen. Null
 *  when no header holds the id or no tab is on screen at all. */
export function neighborOfFolded(items: readonly StripItem[], id: string, dir: 1 | -1): string | null {
  const at = items.findIndex((it) => "head" in it && it.head.ids.includes(id));
  if (at < 0) return null;
  const n = items.length;
  for (let k = 1; k <= n; k++) {
    const it = items[(at + dir * k + n * k) % n];
    if ("id" in it) return it.id;
  }
  return null;
}

/** The header drag: `from` takes `to`'s slot in the FULL union order (every name, not only the
 *  sectioned ones — the persisted order must stay complete). Unknown or equal names → unchanged. */
export function reorderTagOrder(names: readonly string[], from: string, to: string): string[] {
  const list = names.filter((n, i) => typeof n === "string" && names.indexOf(n) === i);
  const fi = list.indexOf(from), ti = list.indexOf(to);
  if (fi < 0 || ti < 0 || fi === ti) return list;
  const out = list.filter((n) => n !== from);
  const at = out.indexOf(to);
  out.splice(fi < ti ? at + 1 : at, 0, from);
  return out;
}

/** Write an order onto a views blob the way the timeline's pill drag does: tagOrder carries the
 *  whole union order (remote-homed names included, viewer-side), and the local tags array re-sorts
 *  to match — the natural store for local-only readers. Returns a new blob. */
export function applyTagOrder(views: SessionViews | null | undefined, order: readonly string[]): SessionViews {
  const nv: SessionViews = JSON.parse(JSON.stringify(views || {}));
  nv.tagOrder = order.slice();
  const ix = new Map(order.map((n, i) => [n, i] as const));
  const rank = (name: string | undefined) => (name !== undefined && ix.has(name) ? ix.get(name)! : order.length);
  nv.tags = viewTags(nv).slice().sort((a, b) => rank(a.name) - rank(b.name));
  delete nv.groups;
  return nv;
}
