import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import Quickshell.Io
import qs.Commons
import qs.Ui

Item {
  id: root
  required property var messages
  property bool shown: false
  property bool active: false
  property bool calling: false
  property var contacts: []
  property var calls: []
  property string selectedNumber: ""
  property string selectedName: ""
  property bool recentOnly: true
  property bool loading: false
  property string error: ""
  readonly property bool editing: search.activeFocus
  signal backRequested()
  signal messagesRequested(string number)
  signal callRequested(string number)
  signal currentCallRequested()

  readonly property var contactsByNumber: {
    var result = {}
    for (var contact of contacts)
      for (var entry of contact.numbers) result[messages.numberKey(entry.number)] = contact
    return result
  }
  readonly property var selectedContact: contactsByNumber[messages.numberKey(selectedNumber)]
  readonly property var selectedNumbers: selectedContact ? selectedContact.numbers
    : selectedNumber ? [{ number: selectedNumber, label: "Phone" }] : []
  readonly property var rows: {
    var term = search.text.trim().toLowerCase()
    if (term || !recentOnly) return contacts.filter(function (item) {
      return !term || item.name.toLowerCase().indexOf(term) !== -1 || item.numbers.some(function (entry) {
        return entry.number.indexOf(term) !== -1 || entry.label.toLowerCase().indexOf(term) !== -1
          || (/^[+0-9 ()-]+$/.test(term) && messages.numberKey(entry.number).indexOf(messages.numberKey(term)) !== -1)
      })
    }).map(function (item) {
      var matched = item.numbers.find(function (entry) {
        return term && (/^[+0-9 ()-]+$/.test(term)
          ? messages.numberKey(entry.number).indexOf(messages.numberKey(term)) !== -1
          : entry.label.toLowerCase() === term)
      })
      return { name: item.name, number: (matched || item.numbers[0]).number, count: item.numbers.length }
    })
    var seen = {}
    var recent = []
    for (var item of calls) {
      var key = messages.numberKey(item.number)
      var contact = contactsByNumber[key]
      var id = contact ? "contact-" + contact.id : "number-" + key
      if (seen[id]) continue
      seen[id] = true
      recent.push({ name: contact ? contact.name : item.number, number: item.number,
                    count: contact ? contact.numbers.length : 1 })
    }
    return recent
  }
  readonly property var recentCalls: selectedNumber ? calls.filter(function (item) {
    return selectedNumbers.some(function (entry) {
      return messages.numberKey(item.number) === messages.numberKey(entry.number)
    })
  }).slice(0, 7) : []

  onActiveChanged: if (active && !contacts.length && !reader.running) reload()
  onShownChanged: if (!shown) {
    selectedNumber = ""
    selectedName = ""
    search.clear()
    contacts = []
    calls = []
    recentOnly = true
  }

  function reload() {
    if (reader.running) return
    loading = contacts.length === 0
    error = ""
    reader.running = true
  }

  function selectContact(number, name) {
    selectedNumber = number
    var contact = contactsByNumber[messages.numberKey(number)]
    selectedName = contact ? contact.name : name || number
    if (number) messages.activateNumber(number, false)
  }

  function callKind(kind) {
    if (kind === 1) return "Incoming"
    if (kind === 2) return "Outgoing"
    if (kind === 3) return "Missed"
    if (kind === 5) return "Rejected"
    if (kind === 6) return "Blocked"
    return "Call"
  }

  Process {
    id: reader
    command: [Qt.resolvedUrl("bin/phone-data").toString().replace(/^file:\/\//, "")]
    stdout: SplitParser {
      onRead: function (line) {
        root.loading = false
        if (!root.shown) return
        try {
          var payload = JSON.parse(line)
          root.error = payload.error ? (root.contacts.length ? "Showing saved contacts and calls." : payload.error) : ""
          if (payload.contacts) root.contacts = payload.contacts
          if (payload.calls) root.calls = payload.calls
          if (root.selectedNumber) {
            var contact = root.contactsByNumber[root.messages.numberKey(root.selectedNumber)]
            if (contact) root.selectedName = contact.name
          }
        } catch (error) {
          root.error = "Could not load contacts and calls."
        }
      }
    }
  }

  component Copy: Text {
    textFormat: Text.PlainText
    color: Color.foreground
    font.family: Style.font.family
    font.pixelSize: Style.font.body
    elide: Text.ElideRight
  }

  ColumnLayout {
    anchors.fill: parent
    spacing: Style.space(10)
    RowLayout {
      Layout.fillWidth: true
      PanelActionButton { iconText: "\u{f004d}"; tooltipText: "Back to phone"; onClicked: root.backRequested() }
      Copy { Layout.fillWidth: true; text: "Calls"; font.pixelSize: Style.font.title; font.bold: true }
      PanelActionButton { iconText: "\u{f0450}"; tooltipText: "Refresh contacts and calls"; onClicked: root.reload() }
      Button { text: "Current call"; bordered: true; onClicked: root.currentCallRequested() }
    }
    PanelSeparator { Layout.fillWidth: true }

    RowLayout {
      Layout.fillWidth: true
      Layout.fillHeight: true
      spacing: Style.space(14)
      ColumnLayout {
        Layout.preferredWidth: Style.space(228)
        Layout.maximumWidth: Style.space(248)
        Layout.fillHeight: true
        TextField { id: search; Layout.fillWidth: true; placeholderText: "Search contacts" }
        RowLayout {
          Button { text: "Recent"; bordered: root.recentOnly; onClicked: { search.clear(); root.recentOnly = true } }
          Button { text: "Contacts"; bordered: !root.recentOnly; onClicked: { search.clear(); root.recentOnly = false } }
        }
        ListView {
          id: contactsList
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: root.rows
          Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }
          delegate: Rectangle {
            required property var modelData
            width: contactsList.width - Style.space(8)
            height: Style.space(48)
            radius: Style.cornerRadius
            color: root.messages.numberKey(modelData.number) === root.messages.numberKey(root.selectedNumber)
              ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.18)
              : contactMouse.containsMouse ? Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.08) : "transparent"
            Column {
              anchors.fill: parent
              anchors.margins: Style.space(7)
              spacing: Style.space(3)
              Copy { width: parent.width; text: modelData.name; font.bold: true }
              Copy { width: parent.width; text: modelData.count > 1 ? modelData.count + " numbers" : modelData.number; font.pixelSize: Style.font.caption; opacity: 0.65 }
            }
            MouseArea {
              id: contactMouse
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: root.selectContact(modelData.number, modelData.name)
            }
          }
          Copy {
            anchors.centerIn: parent
            visible: contactsList.count === 0
            text: root.loading ? "Loading contacts…" : search.text ? "No matching contacts" : "No contacts found"
            opacity: 0.65
          }
        }
      }

      Rectangle { Layout.fillHeight: true; Layout.preferredWidth: 1; color: Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.14) }

      ColumnLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: Style.space(9)
        Copy { Layout.fillWidth: true; text: root.selectedName || "Select a contact"; font.bold: true; font.pixelSize: Style.font.title }
        Column {
          Layout.fillWidth: true
          visible: root.selectedNumbers.length > 0
          spacing: Style.space(3)
          Repeater {
            model: root.selectedNumbers
            Button {
              required property var modelData
              width: parent.width
              text: modelData.label + "  ·  " + modelData.number
              leftAlign: true
              selected: root.messages.numberKey(modelData.number) === root.messages.numberKey(root.selectedNumber)
              bordered: true
              onClicked: root.selectContact(modelData.number, root.selectedName)
            }
          }
        }
        RowLayout {
          visible: !!root.selectedNumber
          Button {
            text: root.calling ? "Calling…" : "Call"
            bordered: true
            enabled: !root.calling
            onClicked: root.callRequested(root.selectedNumber)
          }
          Button { text: "Messages"; bordered: true; onClicked: root.messagesRequested(root.selectedNumber) }
        }

        Controls.ScrollView {
          id: detail
          Layout.fillWidth: true
          Layout.fillHeight: true
          visible: !!root.selectedNumber
          clip: true
          contentWidth: availableWidth
          Column {
            width: detail.availableWidth
            spacing: Style.space(8)
            PanelSectionHeader { text: "Recent calls" }
            Copy { visible: root.recentCalls.length === 0; text: "No recent calls"; opacity: 0.65 }
            Repeater {
              model: root.recentCalls
              Copy {
                required property var modelData
                width: parent.width
                text: root.callKind(modelData.kind) + "  ·  " + root.messages.stamp(modelData.date, false)
                  + (modelData.duration ? "  ·  " + Math.floor(modelData.duration / 60) + ":" + String(modelData.duration % 60).padStart(2, "0") : "")
              }
            }
            PanelSectionHeader { text: "Recent messages" }
            Copy {
              visible: !root.messages.history.length
              text: root.messages.fetching ? "Loading messages…" : "No SMS history"
              opacity: 0.65
            }
            Repeater {
              model: root.messages.history.slice(-5).reverse()
              Column {
                required property var modelData
                width: parent.width
                spacing: Style.space(3)
                Copy {
                  width: parent.width
                  text: (modelData.outgoing ? "You" : "Received") + "  ·  " + root.messages.stamp(modelData.date, false)
                  font.pixelSize: Style.font.caption
                  opacity: 0.65
                }
                Copy {
                  width: parent.width
                  text: modelData.text || "Attachment"
                  wrapMode: Text.Wrap
                  maximumLineCount: 3
                }
              }
            }
          }
        }
      }
    }
    Copy { Layout.fillWidth: true; visible: !!root.error; text: root.error; wrapMode: Text.Wrap }
  }
}
