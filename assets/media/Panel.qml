import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import qs.Commons as Commons

Panel {
  id: root
  moduleName: "local.media-controls"
  ipcTarget: moduleName
  manageIpc: false

  // Snapshot from the helper, keyed by id. The Repeater only sees the id list,
  // so a volume or play-state change updates rows in place instead of
  // rebuilding them (which would restart every spectrum tap).
  property var players: []
  property var byId: ({})
  property var playerIds: []
  property string lastSnapshot: ""
  property int dragging: 0
  property bool controlError: false
  property Item activeCard: null
  // Play states we just asked for. Snapshots that still show the old state
  // are skipped for a moment so a hand-off never flickers back.
  property var expect: null
  readonly property Item liveCard: activeCard && activeCard.isActive ? activeCard : null

  readonly property var activePlayer: players.find(p => p.media && p.playing && p.audible)
    || players.find(p => p.media && p.playing && !p.isPhone)
    || players.find(p => p.media && p.playing)
    || players.find(p => p.media && (p.play || p.pause)) || null
  readonly property string spectrumPath: Qt.resolvedUrl("bin/playback-spectrum").toString().replace(/^file:\/\//, "")
  readonly property string helperPath: Qt.resolvedUrl("bin/media-controls").toString().replace(/^file:\/\//, "")

  readonly property color ink: Color.foreground
  readonly property color accent: Color.accent
  function tint(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }
  function mix(a, b, t) {
    return Qt.rgba(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t, a.a + (b.a - a.a) * t)
  }

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  function refresh() {
    if (opened && !reader.running && !action.running && dragging === 0 && !fader.busy) reader.running = true
  }

  function apply(next) {
    const map = {}
    for (const p of next) map[p.id] = p
    const ids = next.map(p => p.id)
    players = next
    byId = map
    if (ids.join("\n") !== playerIds.join("\n")) playerIds = ids
  }

  function send(command, player, value) {
    if (action.running) return
    controlError = false
    if (command === "focus") {
      const target = byId[player]
      if (target) {
        const states = {}
        const starting = !target.playing
        for (const p of players)
          if (p.media) states[p.id] = p.id === player ? starting : (starting ? false : !!p.playing)
        expect = {at: Date.now(), states: states}
        apply(players.map(p => p.id in states ? Object.assign({}, p, {playing: states[p.id]}) : p))
      }
    }
    action.command = [helperPath, command, player].concat(value === undefined ? [] : [String(value)])
    action.running = true
  }

  // Live volume: drags fire many changes, so only the newest pending value is
  // sent once the previous write finishes. PipeWire streams are written with
  // pactl directly (milliseconds); MPRIS-only players go through the helper.
  QtObject {
    id: fader
    property var pending: null
    readonly property bool busy: writer.running || pending !== null
    function set(player, value) {
      pending = {player: player, value: Math.max(0, Math.min(100, Math.round(value)))}
      flush()
    }
    function flush() {
      if (writer.running || pending === null) return
      const p = pending.player, v = pending.value
      pending = null
      const inputs = (p.sinkInputs || []).filter(i => Number.isInteger(i))
      if (inputs.length)
        writer.command = ["sh", "-c", 'v="$1"; shift; for i; do pactl set-sink-input-volume "$i" "$v%"; done', "sh", String(v)]
          .concat(inputs.map(String))
      else
        writer.command = [root.helperPath, "volume", p.id, String(v)]
      writer.running = true
    }
  }
  Process {
    id: writer
    onExited: function(code) {
      if (code !== 0) root.controlError = true
      if (fader.pending !== null) fader.flush()
      else settle.restart()
    }
  }
  Timer { id: settle; interval: 150; onTriggered: root.refresh() }

  IpcHandler {
    target: "local.media-controls"
    function open() { root.open() }
    function close() { root.close() }
    function toggle() { root.toggle() }
    // Set a source's volume by name, through the same live path as the fader.
    function volume(name: string, value: int): string {
      const p = root.players.find(p => p.name.toLowerCase() === name.toLowerCase())
      if (!p || typeof p.volume !== "number") return "unknown source"
      fader.set(p, value)
      return "ok"
    }
    function status(): string {
      const rows = Array.from({length: appRows.count}, (_, i) => appRows.itemAt(i)).filter(Boolean)
      return JSON.stringify({opened: root.opened, players: root.players.length,
        phones: root.players.filter(p => p.isPhone).length,
        active: root.activePlayer?.name || "", apps: root.players.filter(p => !p.media).length,
        spectra: rows.filter(r => r.hasSignal).map(r => r.p.name),
        adjustable: root.players.filter(p => typeof p.volume === "number").length})
    }
  }

  Process {
    id: reader
    command: [root.helperPath, "list"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (root.dragging > 0 || fader.busy || text === root.lastSnapshot || text.length > 20000) return
        try {
          const next = JSON.parse(text)
          if (!Array.isArray(next) || next.length > 16) return
          if (root.expect) {
            const states = root.expect.states
            const settled = next.every(p => !(p.id in states) || !!p.playing === states[p.id])
            if (!settled && Date.now() - root.expect.at < 2500) { settle.restart(); return }
            root.expect = null
          }
          root.lastSnapshot = text
          root.apply(next)
        } catch (error) {}
      }
    }
  }

  Process {
    id: action
    onExited: function(code) {
      root.controlError = code !== 0
      settle.restart()
      burst.left = 12
      burst.restart()
    }
  }
  // After a hand-off, look for the new stream quickly (a phone only creates
  // its audio stream once it starts playing) so its spectrum rises at once.
  Timer {
    id: burst
    property int left: 0
    interval: 220
    repeat: true
    onTriggered: { root.refresh(); if (--left <= 0) stop() }
  }

  Timer {
    interval: 800
    repeat: true
    triggeredOnStart: true
    running: root.opened
    onTriggered: root.refresh()
  }

  onOpenedChanged: if (!opened) { dragging = 0; controlError = false }

  implicitWidth: mediaButton.implicitWidth
  implicitHeight: mediaButton.implicitHeight
  BarIconButton {
    id: mediaButton
    anchors.fill: parent
    bar: root.bar
    text: "\u{f0387}"
    tooltipText: strings.t("title")
    active: root.opened
    onPressed: root.toggle()
  }

  component Label: Text {
    textFormat: Text.PlainText
    font.family: Style.font.family
    font.pixelSize: Style.font.body
    color: root.ink
    elide: Text.ElideRight
  }

  // Bare glyph transport control: no fill, just high-contrast ink that warms
  // to the accent on hover and gives under the finger when pressed.
  component Transport: Item {
    id: control
    property string glyph: ""
    property string altGlyph: ""
    property bool showAlt: false
    property real size: Style.font.display
    property string label: ""
    signal activated()

    implicitWidth: Math.round(size * 1.6)
    implicitHeight: Math.round(size * 1.6)
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: label
    Accessible.onPressAction: if (enabled) activated()
    Keys.onSpacePressed: if (enabled) activated()
    Keys.onReturnPressed: if (enabled) activated()
    opacity: enabled ? 1 : 0.28
    Behavior on opacity { NumberAnimation { duration: Style.duration(160) } }

    readonly property color tone: !enabled ? root.ink
      : (press.pressed || press.containsMouse || activeFocus) ? root.accent : root.ink
    scale: press.pressed ? 0.86 : press.containsMouse ? 1.08 : 1
    Behavior on scale { NumberAnimation { duration: Style.duration(140); easing.type: Easing.OutBack } }

    Text {
      textFormat: Text.PlainText
      anchors.centerIn: parent
      text: control.glyph
      font.family: Style.font.family
      font.pixelSize: control.size
      color: control.tone
      opacity: control.showAlt ? 0 : 1
      scale: control.showAlt ? 0.6 : 1
      rotation: control.showAlt ? -90 : 0
      Behavior on opacity { NumberAnimation { duration: Style.duration(180) } }
      Behavior on scale { NumberAnimation { duration: Style.duration(220); easing.type: Easing.OutCubic } }
      Behavior on rotation { NumberAnimation { duration: Style.duration(220); easing.type: Easing.OutCubic } }
      Behavior on color { ColorAnimation { duration: Style.duration(120) } }
    }
    Text {
      textFormat: Text.PlainText
      visible: control.altGlyph !== ""
      anchors.centerIn: parent
      text: control.altGlyph
      font.family: Style.font.family
      font.pixelSize: control.size
      color: control.tone
      opacity: control.showAlt ? 1 : 0
      scale: control.showAlt ? 1 : 0.6
      rotation: control.showAlt ? 0 : 90
      Behavior on opacity { NumberAnimation { duration: Style.duration(180) } }
      Behavior on scale { NumberAnimation { duration: Style.duration(220); easing.type: Easing.OutCubic } }
      Behavior on rotation { NumberAnimation { duration: Style.duration(220); easing.type: Easing.OutCubic } }
      Behavior on color { ColorAnimation { duration: Style.duration(120) } }
    }
    // Keyboard focus mark: one block under the glyph.
    Rectangle {
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.bottom
      width: Style.space(10)
      height: Style.space(2)
      color: root.accent
      visible: control.activeFocus
    }
    MouseArea {
      id: press
      anchors.fill: parent
      hoverEnabled: true
      enabled: control.enabled
      cursorShape: Qt.PointingHandCursor
      onClicked: control.activated()
    }
  }

  // Block spectrum: bass on the left, treble on the right, rising from the
  // bottom. Columns left of `split` are lit with the accent, the rest are dim.
  component Spectrum: Canvas {
    id: spec
    property var levels: []
    property var peaks: []
    property real split: width
    property real litAlpha: 0.4
    property real dimAlpha: 0.13
    property bool restRow: false
    property real capAlpha: Math.min(1, litAlpha * 2.2)
    property real curve: 1.6
    property real bw: Math.max(3, Style.space(4))
    property real gap: Math.max(1, Style.space(1))
    property real bh: Math.max(2, Style.space(2))
    readonly property int columns: Math.max(8, Math.floor((width + gap) / (bw + gap)))
    readonly property int rows: Math.max(3, Math.floor((height + gap) / (bh + gap)))
    property color lit: root.accent
    property color dim: root.ink
    onLevelsChanged: requestPaint()
    onSplitChanged: requestPaint()
    onLitChanged: requestPaint()
    onWidthChanged: requestPaint()

    function sample(list, c) {
      if (!list.length) return 0
      const f = c * (list.length - 1) / Math.max(1, columns - 1)
      const i = Math.floor(f), t = f - i
      return (list[i] || 0) * (1 - t) + (list[Math.min(list.length - 1, i + 1)] || 0) * t
    }

    onPaint: {
      const ctx = getContext("2d")
      ctx.clearRect(0, 0, width, height)
      const stepX = bw + gap, stepY = bh + gap
      // Centre the grid so leftover pixels split evenly on both sides.
      const x0 = Math.floor((width - (columns * stepX - gap)) / 2)
      const litFill = root.tint(lit, litAlpha), dimFill = root.tint(dim, dimAlpha)
      const litCap = root.tint(lit, capAlpha), dimCap = root.tint(dim, Math.min(1, dimAlpha * 2.4))
      for (let c = 0; c < columns; ++c) {
        const x = x0 + c * stepX
        const on = x + bw / 2 <= split
        const n = Math.min(rows, Math.round(Math.pow(sample(levels, c) / 100, curve) * rows))
        const shown = restRow ? Math.max(1, n) : n
        ctx.fillStyle = on ? litFill : dimFill
        for (let r = 0; r < shown; ++r) ctx.fillRect(x, height - (r + 1) * stepY + gap, bw, bh)
        const pk = Math.min(rows, Math.round(Math.pow(sample(peaks, c) / 100, curve) * rows))
        if (pk > n && pk > 0) {
          ctx.fillStyle = on ? litCap : dimCap
          ctx.fillRect(x, height - pk * stepY + gap, bw, bh)
        }
      }
    }
  }

  KeyboardPanel {
    id: popup
    anchorItem: mediaButton
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keys
    contentWidth: popup.fittedContentWidth(Style.space(380))
    contentHeight: popup.fittedContentHeight(Math.min(Style.space(620), column.implicitHeight))

    PanelKeyCatcher {
      id: keys
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: root.dragging === 0

        Column {
          id: column
          width: parent.width
          spacing: Style.space(6)

          // ── Now playing ──────────────────────────────────────────────────
          Item {
            id: hero
            readonly property var p: root.activePlayer
            visible: p !== null
            width: column.width
            height: visible ? heroColumn.implicitHeight + Style.space(14) : 0

            Column {
              id: heroColumn
              anchors.top: parent.top
              anchors.topMargin: Style.space(8)
              width: parent.width
              spacing: Style.space(2)

              Label {
                id: sourceLabel
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                property string source: hero.p?.id || ""
                onSourceChanged: sourceSwap.restart()
                NumberAnimation { id: sourceSwap; target: sourceLabel; property: "opacity"; from: 0; to: 1; duration: Style.duration(380); easing.type: Easing.OutCubic }
                text: (hero.p?.playing ? "" : strings.t("paused").toUpperCase() + "  ·  ")
                  + (hero.p?.name || "").toUpperCase()
                color: root.tint(root.ink, 0.62)
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 2.4
              }

              Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: Style.space(14)
                Transport {
                  anchors.verticalCenter: parent.verticalCenter
                  glyph: "\u{f04ae}"
                  size: Math.round(Style.font.display * 1.15)
                  label: strings.t("previous")
                  enabled: !!hero.p?.previous
                  onActivated: root.send("previous", hero.p.id)
                }
                Transport {
                  anchors.verticalCenter: parent.verticalCenter
                  glyph: "\u{f040a}"
                  altGlyph: "\u{f03e4}"
                  showAlt: !!hero.p?.playing
                  size: Math.round(Style.font.displayLarge * 1.55)
                  label: strings.t(hero.p?.playing ? "pause" : "play")
                  enabled: hero.p?.playing ? !!hero.p.pause : !!hero.p?.play
                  onActivated: root.send("focus", hero.p.id)
                }
                Transport {
                  anchors.verticalCenter: parent.verticalCenter
                  glyph: "\u{f04ad}"
                  size: Math.round(Style.font.display * 1.15)
                  label: strings.t("next")
                  enabled: !!hero.p?.next
                  onActivated: root.send("next", hero.p.id)
                }
              }

              // Track, sliding in when it changes.
              Item {
                id: track
                width: parent.width
                height: trackColumn.implicitHeight
                readonly property string key: (hero.p?.id || "") + "\n" + (hero.p?.title || "") + "\n" + (hero.p?.artist || "")
                onKeyChanged: swap.restart()
                Column {
                  id: trackColumn
                  width: parent.width
                  spacing: Style.space(1)
                  Label {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    text: hero.p?.title || hero.p?.name || ""
                    font.pixelSize: Style.font.heading
                    font.bold: true
                  }
                  Label {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    visible: text.length > 0
                    text: hero.p?.artist || ""
                    color: root.tint(root.ink, 0.6)
                  }
                }
                ParallelAnimation {
                  id: swap
                  NumberAnimation { target: trackColumn; property: "opacity"; from: 0; to: 1; duration: Style.duration(320); easing.type: Easing.OutCubic }
                  NumberAnimation { target: trackColumn; property: "y"; from: Style.space(6); to: 0; duration: Style.duration(320); easing.type: Easing.OutCubic }
                }
              }
            }
          }

          Item { width: 1; height: Style.space(10); visible: root.players.length > 0 }

          Label {
            visible: root.players.length === 0
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            topPadding: Style.space(18)
            bottomPadding: Style.space(18)
            text: strings.t("empty")
            color: root.tint(root.ink, 0.55)
          }

          // ── Sources: the whole card is the volume fader ──────────────────
          Repeater {
            id: appRows
            model: root.playerIds

            Item {
              id: card
              required property string modelData
              readonly property var p: root.byId[modelData] || ({})

              width: column.width
              height: Style.space(48)
              activeFocusOnTab: true
              Accessible.role: Accessible.Slider
              Accessible.name: p.name || ""

              readonly property bool adjustable: typeof p.volume === "number"
              readonly property bool on: p.media ? !!p.playing : !p.muted
              readonly property bool canToggle: !p.media || (p.playing ? !!p.pause : !!p.play)
              readonly property bool isActive: root.activePlayer !== null && root.activePlayer.id === p.id
              readonly property bool dragging: handle.pressed
              readonly property bool hot: toggleMouse.containsMouse || handle.containsMouse || handle.pressed
              property real preview: -1
              readonly property real percent: preview >= 0 ? preview : (p.volume || 0)
              readonly property bool hasSignal: live
              // Sounding sources take the window's own background so the
              // spectrum glows; everything else greys out. `stage` eases
              // between the two so a hand-off crossfades instead of snapping.
              readonly property bool sounding: live || levels.length > 0 || (on && !!p.media && !!p.playing)
              property real stage: sounding ? 1 : 0
              Behavior on stage { NumberAnimation { duration: Style.duration(420); easing.type: Easing.InOutQuad } }

              Binding { target: root; property: "activeCard"; value: card; when: card.isActive }

              // Spectrum state, smoothed in band space so the hero strip can
              // share it: fast attack, eased release, peaks that hold then fall.
              property bool live: false
              property var levels: []
              property var peaks: []
              property var holds: []
              property double lastTick: 0
              function tick() {
                const now = Date.now()
                const dt = Math.min(0.1, lastTick ? (now - lastTick) / 1000 : 0.016)
                lastTick = now
                const attack = 1 - Math.exp(-dt / 0.035)
                const release = 1 - Math.exp(-dt / 0.16)
                let target = null
                for (let i = 0; i < captures.count; ++i) {
                  const s = captures.itemAt(i)?.samples
                  if (!s || !s.length) continue
                  if (!target) target = s.slice()
                  else for (let b = 0; b < target.length; ++b) target[b] = Math.max(target[b], s[b])
                }
                live = root.opened && target !== null
                const n = target ? target.length : levels.length
                const nl = [], np = [], nh = []
                let any = false
                for (let b = 0; b < n; ++b) {
                  const t = target ? target[b] : 0
                  const prev = levels[b] || 0
                  // Rise quickly, fall on an exponential with a linear floor
                  // so bars always land instead of hovering near zero.
                  const v = t > prev ? prev + (t - prev) * attack
                    : Math.max(t, prev - Math.max(35 * dt, (prev - t) * release))
                  let pk = peaks[b] || 0, hold = holds[b] || 0
                  if (v >= pk) { pk = v; hold = 0.32 }
                  else if (hold > 0) hold -= dt
                  else pk = Math.max(v, pk - 55 * dt)
                  nl.push(v); np.push(pk); nh.push(hold)
                  if (v > 0.5 || pk > 0.5) any = true
                }
                if (!any && !levels.length) { lastTick = 0; return }
                levels = any ? nl : []
                peaks = any ? np : []
                holds = any ? nh : []
              }
              Timer {
                interval: 16
                repeat: true
                running: root.opened && (card.live || card.levels.length > 0)
                onTriggered: card.tick()
                onRunningChanged: if (!running) card.lastTick = 0
              }

              // Drop the optimistic value once the helper reports it back.
              onPChanged: {
                if (preview >= 0 && !dragging && Math.abs((p.volume || 0) - preview) < 1.5) preview = -1
                const next = JSON.stringify(p.taps || [])
                if (next !== tapsKey) { tapsKey = next; taps = p.taps || [] }
              }
              property string tapsKey: ""
              property var taps: []
              Timer { id: forgetPreview; interval: 2500; onTriggered: if (!card.dragging) card.preview = -1 }

              function toggle() {
                if (!canToggle) return
                root.send(p.media ? "focus" : "toggle", p.id)
              }
              function setVolume(value) {
                if (!adjustable) return
                preview = Math.max(0, Math.min(100, value))
                fader.set(p, preview)
                forgetPreview.restart()
              }
              Keys.onSpacePressed: toggle()
              Keys.onReturnPressed: toggle()
              Keys.onLeftPressed: setVolume(Math.round(percent) - 5)
              Keys.onRightPressed: setVolume(Math.round(percent) + 5)

              Repeater {
                id: captures
                model: card.taps
                Item {
                  id: tap
                  required property var modelData
                  property var samples: []
                  // A paused stream stops sending; let the bars fall right away.
                  Timer { id: expire; interval: 110; onTriggered: tap.samples = [] }
                  Process {
                    command: [root.spectrumPath, String(tap.modelData.index), tap.modelData.monitor]
                    running: root.opened
                    stdout: SplitParser {
                      onRead: function(line) {
                        if (!root.opened || line.length > 600) return
                        try {
                          const bands = JSON.parse(line)
                          if (!Array.isArray(bands) || (bands.length !== 48 && bands.length !== 0)) return
                          tap.samples = bands
                          if (bands.length) { expire.restart(); card.live = true }
                        } catch (error) {}
                      }
                    }
                    onExited: tap.samples = []
                  }
                }
              }

              Rectangle {
                id: surface
                anchors.fill: parent
                radius: Style.cornerRadius
                clip: true
                color: root.mix(root.mix(Color.popups.background, root.ink, card.hot ? 0.09 : 0.06),
                  root.mix(Color.popups.background, root.ink, card.hot ? 0.03 : 0), card.stage)
                border.width: 1
                border.color: card.activeFocus ? root.accent
                  : card.isActive ? root.tint(root.accent, 0.55)
                  : root.tint(root.ink, card.hot ? 0.22 : 0.07 + 0.07 * card.stage)
                Behavior on border.color { ColorAnimation { duration: Style.duration(260) } }

                // Volume fill
                Rectangle {
                  id: fill
                  visible: card.adjustable
                  width: parent.width * card.percent / 100
                  height: parent.height
                  color: root.mix(root.tint(root.ink, 0.05), root.tint(root.accent, 0.08), card.stage)
                  Behavior on width {
                    enabled: !card.dragging
                    NumberAnimation { duration: Style.duration(240); easing.type: Easing.OutCubic }
                  }
                }

                Spectrum {
                  anchors.fill: parent
                  anchors.margins: 1
                  levels: card.levels
                  peaks: card.peaks
                  split: card.adjustable ? fill.width - 1 : width
                  lit: root.mix(root.ink, root.accent, card.stage)
                  litAlpha: 0.1 + 0.2 * card.stage
                  dimAlpha: 0.05 + 0.06 * card.stage
                  capAlpha: 0.16 + 0.46 * card.stage
                }

                RowLayout {
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(12)
                  anchors.rightMargin: Style.space(12)
                  spacing: Style.space(10)
                  opacity: 0.45 + 0.55 * card.stage

                  Label {
                    text: card.p.isPhone ? "\u{f03f2}"
                      : card.p.media ? (card.on ? "\u{f075a}" : "\u{f03e4}")
                      : (card.on ? "\u{f057e}" : "\u{f075f}")
                    font.pixelSize: Style.font.icon
                    color: root.mix(root.ink, root.accent, card.stage)
                    Layout.preferredWidth: Style.space(16)
                    horizontalAlignment: Text.AlignHCenter
                  }
                  ColumnLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignVCenter
                    spacing: 0
                    Label {
                      Layout.fillWidth: true
                      text: card.p.name || ""
                      font.pixelSize: Style.font.subtitle
                      font.bold: true
                    }
                    Label {
                      Layout.fillWidth: true
                      visible: text.length > 0
                      text: card.p.media
                        ? [card.p.title, card.p.artist].filter(s => s).join(" · ") || (card.on ? "" : strings.t("paused"))
                        : (card.on ? "" : strings.t("muted"))
                      font.pixelSize: Style.font.bodySmall
                      color: root.tint(root.ink, 0.66)
                    }
                  }
                  Label {
                    visible: card.adjustable
                    text: Commons.I18n.formatNumber(Math.round(card.percent))
                    font.pixelSize: Style.font.subtitle
                    font.bold: true
                    color: card.dragging ? root.accent : root.tint(root.ink, 0.85)
                    horizontalAlignment: Text.AlignRight
                    Layout.minimumWidth: Style.space(26)
                  }
                }

                // Fader line
                Rectangle {
                  visible: card.adjustable
                  x: Math.max(0, Math.min(card.width - width, fill.width - width / 2))
                  width: card.hot ? Style.space(3) : Style.space(2)
                  height: parent.height
                  color: root.mix(root.tint(root.ink, 0.5), root.accent, card.stage)
                  Behavior on width { NumberAnimation { duration: Style.duration(100) } }
                }
              }

              // Click anywhere: play/pause media, mute/unmute apps.
              MouseArea {
                id: toggleMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: card.canToggle ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: { card.forceActiveFocus(); card.toggle() }
              }

              // Grab the line to fade. The value is written while dragging.
              MouseArea {
                id: handle
                visible: card.adjustable
                x: Math.max(0, Math.min(card.width - width, fill.width - width / 2))
                width: Style.space(22)
                height: parent.height
                hoverEnabled: true
                preventStealing: true
                cursorShape: Qt.SizeHorCursor
                function track(mouse) {
                  const point = mapToItem(card, mouse.x, mouse.y)
                  card.setVolume(point.x / card.width * 100)
                }
                onPressed: function(mouse) { root.dragging++; card.forceActiveFocus(); card.preview = card.percent }
                onPositionChanged: function(mouse) { if (pressed) track(mouse) }
                onReleased: root.dragging = Math.max(0, root.dragging - 1)
                onCanceled: root.dragging = Math.max(0, root.dragging - 1)
              }
            }
          }

          Label {
            width: parent.width
            visible: root.controlError
            text: strings.t("error")
            color: Color.urgent
          }
        }
      }
    }
  }
}
