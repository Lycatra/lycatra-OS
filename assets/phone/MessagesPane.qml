import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import Quickshell.Io
import qs.Commons
import qs.Ui

Item {
  id: root
  required property string deviceId
  property bool shown: false
  property color foreground: Color.foreground
  property bool callsAvailable: false
  property bool calling: false
  property string callStatus: ""
  property var pendingNumber: null
  readonly property var selectedThread: threads.find(function (item) { return item.thread === selected })
  signal backRequested()
  signal callsRequested(string number)
  signal contactRequested(string number)
  readonly property bool editing: search.activeFocus || recipient.activeFocus || draft.activeFocus
  property var threads: []
  property var history: []
  property string selected: ""
  property var contactNames: ({})
  property bool composing: false
  property bool loading: true
  property bool fetching: false
  property bool sending: false
  property bool exhausted: false
  property string feedback: ""
  property bool feedbackError: false
  property bool preserveScroll: false
  property real scrollBefore: 0
  property real heightBefore: 0
  property int requestedStart: 0
  readonly property bool compact: width < Style.space(550)
  readonly property var shownThreads: {
    var query = search.text.toLowerCase().trim()
    return !query ? threads : threads.filter(function (item) {
      return (conversationName(item) + " " + item.label + " " + item.text).toLowerCase().indexOf(query) !== -1
    })
  }

  function reset() {
    threads = []
    history = []
    selected = ""
    contactNames = ({})
    composing = false
    loading = true
    fetching = false
    sending = false
    exhausted = false
    feedback = ""
    search.clear()
    recipient.clear()
    draft.clear()
    pendingNumber = null
    requestTimeout.stop()
    finishBatch.stop()
  }

  onShownChanged: if (!shown) reset()
  onDeviceIdChanged: reset()

  function command(action, args) {
    if (!bridge.running) return
    bridge.write(JSON.stringify(Object.assign({ action: action }, args || {})) + "\n")
  }

  function updateThread(message) {
    var current = threads.find(function (item) { return item.thread === message.thread })
    if (current && current.date > message.date) return
    threads = threads.filter(function (item) { return item.thread !== message.thread })
    threads = [message].concat(threads).sort(function (a, b) { return b.date - a.date })
  }

  function selectThread(item) {
    if (sending || selected === item.thread) return
    selected = item.thread
    composing = false
    history = []
    exhausted = false
    feedback = ""
    draft.clear()
    fetch(false)
  }

  function fetch(older) {
    preserveScroll = older
    scrollBefore = chat.contentY
    heightBefore = chat.contentHeight
    requestedStart = older ? history.length : 0
    finishBatch.stop()
    fetching = true
    requestTimeout.restart()
    command("history", { thread: selected, start: requestedStart })
  }

  function numberKey(number) {
    var digits = String(number || "").replace(/\D/g, "")
    if (digits.length === 11 && digits.slice(0, 2) === "41") return "0" + digits.slice(2)
    if (digits.length === 13 && digits.slice(0, 4) === "0041") return "0" + digits.slice(4)
    return digits
  }

  function loadContactNames(contacts) {
    var names = {}
    for (var contact of contacts) {
      for (var number of contact.numbers) {
        var key = numberKey(number)
        if (!key) continue
        if (names[key] === undefined) names[key] = contact.name
        else if (names[key] !== contact.name) names[key] = ""
      }
    }
    contactNames = names
  }

  function conversationName(thread) {
    if (!thread) return ""
    if (!thread.addresses.length) return thread.label
    return thread.addresses.map(function (address) {
      return contactNames[numberKey(address)] || address
    }).join(", ")
  }

  function activateNumber(number, composeIfMissing) {
    if (!number) return
    if (loading) {
      pendingNumber = { number: number, composeIfMissing: composeIfMissing }
      return
    }
    var key = numberKey(number)
    var thread = threads.find(function (item) {
      return key && item.addresses.length === 1 && numberKey(item.addresses[0]) === key
    })
    if (thread) {
      selectThread(thread)
    } else if (composeIfMissing) {
      newMessage()
      recipient.text = number
      Qt.callLater(function () { draft.forceActiveFocus() })
    } else {
      selected = ""
      history = []
      composing = false
      fetching = false
    }
  }

  function newMessage() {
    if (sending) return
    selected = ""
    composing = true
    history = []
    fetching = false
    feedback = ""
    draft.clear()
    recipient.clear()
    Qt.callLater(function () { recipient.forceActiveFocus() })
  }

  function send() {
    if (sending || !draft.text.trim() || (!selected && (!composing || !recipient.text.trim()))) return
    sending = true
    feedback = ""
    command(composing ? "compose" : "reply", {
      thread: selected, number: recipient.text, text: draft.text
    })
  }

  function receive(line) {
    var result
    try { result = JSON.parse(line) } catch (error) { return }
    if (result.event === "ready") {
      command("refresh")
    } else if (result.event === "contacts") {
      loadContactNames(result.contacts)
    } else if (result.event === "threads") {
      threads = result.threads
      loading = false
      if (pendingNumber) {
        var target = pendingNumber
        pendingNumber = null
        activateNumber(target.number, target.composeIfMissing)
      }
    } else if (result.event === "historyCache" && result.thread === selected) {
      var known = new Set(history.map(function (item) { return item.id }))
      var cached = result.messages.filter(function (item) { return !known.has(item.id) })
      if (cached.length) {
        history = history.concat(cached).sort(function (a, b) { return a.date - b.date })
        Qt.callLater(function () {
          if (root.preserveScroll) chat.contentY = root.scrollBefore + chat.contentHeight - root.heightBefore
          else chat.positionViewAtEnd()
        })
      }
    } else if (result.event === "message") {
      var item = result.message
      updateThread(item)
      if (item.thread !== selected) return
      var follow = chat.atYEnd
      if (!history.some(function (entry) { return entry.id === item.id })) {
        history = history.concat([item]).sort(function (a, b) { return a.date - b.date })
        if (!fetching && follow) Qt.callLater(function () { chat.positionViewAtEnd() })
      }
      if (fetching) finishBatch.restart()
    } else if (result.event === "loaded" && result.thread === selected && fetching) {
      finishBatch.restart()
    } else if (result.event === "removed") {
      threads = threads.filter(function (item) { return item.thread !== result.thread })
      if (selected === result.thread) { selected = ""; history = [] }
    } else if (result.event === "submitted") {
      sending = false
      draft.clear()
      feedbackError = false
      feedback = "Handed to phone"
      command("refresh")
    } else if (result.event === "error") {
      if (result.action === "history") fetching = false
      if (result.action === "refresh") loading = false
      sending = false
      feedback = (result.action === "history" && history.length) || (result.action === "refresh" && threads.length)
        ? "Showing saved messages." : result.text
      feedbackError = true
    }
  }

  function stamp(ms, shortForm) {
    if (ms <= 0) return ""
    var date = new Date(ms)
    if (date.toDateString() === new Date().toDateString())
      return Qt.locale().toString(date, "HH:mm")
    return Qt.formatDateTime(date, shortForm ? "MMM d" : "MMM d · HH:mm")
  }

  Process {
    id: bridge
    command: [Qt.resolvedUrl("bin/messages-bridge").toString().replace(/^file:\/\//, ""), root.deviceId]
    stdinEnabled: true
    running: root.shown && root.deviceId !== ""
    stdout: SplitParser { onRead: function (line) { root.receive(line) } }
    onExited: function (code) {
      if (!root.shown || !code) return
      root.loading = false
      root.fetching = false
      root.sending = false
      root.feedbackError = true
      root.feedback = "Could not open messages."
    }
  }

  Timer {
    interval: 10000
    repeat: true
    running: root.shown
    onTriggered: if (!root.sending && !root.fetching) root.command("refresh")
  }

  Timer {
    id: finishBatch
    interval: 250
    onTriggered: {
      if (!root.fetching) return
      root.fetching = false
      requestTimeout.stop()
      root.exhausted = root.history.length - root.requestedStart < 35
      Qt.callLater(function () {
        if (root.preserveScroll) chat.contentY = root.scrollBefore + chat.contentHeight - root.heightBefore
        else chat.positionViewAtEnd()
      })
    }
  }

  Timer {
    id: requestTimeout
    interval: 12000
    onTriggered: {
      if (!root.fetching) return
      root.fetching = false
      root.feedbackError = true
      root.feedback = root.history.length ? "Showing available messages." : "No history received. Try again."
    }
  }

  component Caption: Text {
    color: root.foreground
    textFormat: Text.PlainText
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
    opacity: 0.64
  }

  ColumnLayout {
    anchors.fill: parent
    spacing: Style.space(12)

    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(10)
      PanelActionButton {
        iconText: "\u{f004d}"
        tooltipText: "Back to phone"
        foreground: root.foreground
        onClicked: root.backRequested()
      }
      Text {
        textFormat: Text.PlainText
        Layout.fillWidth: true
        text: "Messages"
        color: root.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.title
        font.bold: true
      }
      PanelActionButton {
        iconText: "\u{f0450}"
        tooltipText: "Refresh messages"
        foreground: root.foreground
        onClicked: root.command("refresh")
      }
    }

    PanelSeparator { Layout.fillWidth: true; foreground: root.foreground }

    RowLayout {
      Layout.fillWidth: true
      Layout.fillHeight: true
      spacing: Style.space(14)

      ColumnLayout {
        visible: !root.compact || (!root.selected && !root.composing)
        Layout.fillWidth: root.compact
        Layout.preferredWidth: Style.space(234)
        Layout.maximumWidth: root.compact ? 16777215 : Style.space(252)
        Layout.fillHeight: true
        spacing: Style.space(8)

        TextField {
          id: search
          Layout.fillWidth: true
          placeholderText: "Search conversations"
          foreground: root.foreground
        }

        ListView {
          id: inbox
          Layout.fillWidth: true
          Layout.fillHeight: true
          spacing: Style.space(3)
          clip: true
          model: root.shownThreads
          Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }

          delegate: Rectangle {
            required property var modelData
            width: inbox.width - Style.space(8)
            height: preview.implicitHeight + Style.space(16)
            radius: Style.cornerRadius
            color: modelData.thread === root.selected ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.16)
              : pointer.containsMouse ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.07) : "transparent"

            ColumnLayout {
              id: preview
              anchors.fill: parent
              anchors.margins: Style.space(8)
              spacing: Style.space(4)
              RowLayout {
                Layout.fillWidth: true
                Text {
                  Layout.fillWidth: true
                  text: root.conversationName(modelData)
                  textFormat: Text.PlainText
                  color: root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  font.bold: true
                  elide: Text.ElideRight
                }
                Rectangle {
                  visible: modelData.unread
                  width: Style.space(5)
                  height: width
                  radius: Style.cornerRadius
                  color: Color.accent
                }
                Caption { text: root.stamp(modelData.date, true) }
              }
              Caption {
                Layout.fillWidth: true
                text: modelData.text.replace(/\s+/g, " ") || (modelData.attachments ? "Attachment" : "")
                elide: Text.ElideRight
              }
            }

            MouseArea {
              id: pointer
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: root.selectThread(modelData)
            }
          }

          Caption {
            anchors.centerIn: parent
            visible: inbox.count === 0
            text: root.loading ? "Loading conversations…" : search.text ? "No matches" : "No conversations yet"
          }
        }
      }

      Rectangle {
        visible: !root.compact
        Layout.fillHeight: true
        Layout.preferredWidth: 1
        color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14)
      }

      ColumnLayout {
        visible: !root.compact || root.selected !== "" || root.composing
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: Style.space(8)

        RowLayout {
          visible: root.selected !== "" || root.composing
          Layout.fillWidth: true
          PanelActionButton {
            visible: root.compact
            iconText: "\u{f004d}"
            tooltipText: "Conversations"
            foreground: root.foreground
            onClicked: { root.selected = ""; root.composing = false; root.history = []; draft.clear() }
          }
          Item {
            id: contactTitle
            Layout.fillWidth: true
            implicitHeight: contactLabel.implicitHeight + Style.space(8)
            readonly property bool canOpen: root.callsAvailable && !!root.selectedThread
              && root.selectedThread.addresses.length === 1
            activeFocusOnTab: canOpen

            function openContact() {
              if (canOpen) root.contactRequested(root.selectedThread.addresses[0])
            }
            Keys.onReturnPressed: openContact()
            Keys.onSpacePressed: openContact()

            Text {
              id: contactLabel
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: root.composing ? "New message" : root.conversationName(root.selectedThread)
              textFormat: Text.PlainText
              color: contactHover.hovered || contactTitle.activeFocus ? Color.accent : root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.bold: true
              font.underline: contactHover.hovered || contactTitle.activeFocus
              elide: Text.ElideRight
            }

            HoverHandler { id: contactHover; enabled: contactTitle.canOpen; cursorShape: Qt.PointingHandCursor }
            TapHandler { enabled: contactTitle.canOpen; onTapped: contactTitle.openContact() }
          }
          Button {
            visible: root.callsAvailable && !!root.selectedThread && root.selectedThread.addresses.length === 1
            text: "Call"
            bordered: true
            enabled: !root.calling
            onClicked: root.callsRequested(root.selectedThread.addresses[0])
          }
          Button {
            visible: root.selected !== "" && !root.exhausted
            text: root.fetching ? "Loading…" : "Load older"
            foreground: root.foreground
            enabled: !root.fetching
            onClicked: root.fetch(true)
          }
        }

        TextField {
          id: recipient
          Layout.fillWidth: true
          visible: root.composing
          placeholderText: "Phone number"
          foreground: root.foreground
          maximumLength: 32
          onAccepted: draft.forceActiveFocus()
        }

        ListView {
          id: chat
          Layout.fillWidth: true
          Layout.fillHeight: true
          spacing: Style.space(8)
          clip: true
          model: root.history
          Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }

          delegate: Item {
            required property var modelData
            width: chat.width - Style.space(8)
            height: bubble.implicitHeight
            Rectangle {
              id: bubble
              anchors.right: modelData.outgoing ? parent.right : undefined
              anchors.left: modelData.outgoing ? undefined : parent.left
              width: Math.min(parent.width * 0.86, Math.max(Style.space(104), messageBody.implicitWidth + Style.space(20)))
              implicitHeight: bubbleText.implicitHeight + Style.space(16)
              height: implicitHeight
              radius: Style.cornerRadius
              color: modelData.outgoing ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.20)
                : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.08)

              Column {
                id: bubbleText
                anchors.fill: parent
                anchors.margins: Style.space(8)
                spacing: Style.space(4)
                TextEdit {
                  id: messageBody
                  width: parent.width
                  text: modelData.text
                  visible: text !== ""
                  textFormat: TextEdit.PlainText
                  readOnly: true
                  selectByMouse: true
                  wrapMode: TextEdit.Wrap
                  color: root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                }
                Caption { visible: modelData.attachments > 0; text: modelData.attachments === 1 ? "1 attachment on phone" : modelData.attachments + " attachments on phone" }
                Caption { text: root.stamp(modelData.date, false) }
              }
            }
          }

          Caption {
            anchors.centerIn: parent
            visible: chat.count === 0
            text: root.fetching ? "Loading messages…" : root.selected ? "No messages loaded" : root.composing ? "" : "Select a conversation"
          }
        }

        RowLayout {
          visible: root.selected !== "" || root.composing
          Layout.fillWidth: true
          spacing: Style.space(8)
          TextField {
            id: draft
            Layout.fillWidth: true
            placeholderText: root.composing ? "Write a message" : "Write a reply"
            maximumLength: 4000
            foreground: root.foreground
            enabled: !root.sending
            onAccepted: root.send()
          }
          Button {
            text: root.sending ? "Sending…" : "Send"
            foreground: root.foreground
            bordered: true
            enabled: bridge.running && !root.sending && draft.text.trim() !== "" && (!root.composing || recipient.text.trim() !== "")
            onClicked: root.send()
          }
        }
      }
    }

    Caption {
      Layout.fillWidth: true
      visible: text !== ""
      text: root.feedback
      opacity: root.feedbackError ? 1 : 0.64
      color: root.feedbackError ? Color.accent : root.foreground
      wrapMode: Text.Wrap
    }
  }
}
