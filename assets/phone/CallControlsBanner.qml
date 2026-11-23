import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

Rectangle {
  id: root
  readonly property var buttonWidths: [muteButton.width, shareButton.width, endButton.width]
  property bool active: false
  property bool canHangUp: true
  property string contact: ""
  property string number: ""
  property string state: ""
  property string audio: ""
  property bool muted: false
  property bool sharing: false
  property bool failed: false
  property double connectedAt: 0
  property double clock: Date.now()
  signal hangupRequested()
  signal muteRequested()
  signal shareRequested()

  readonly property bool audioConnected: audio.startsWith("Audio connected")
  readonly property int seconds: connectedAt > 0 ? Math.max(0, Math.floor((clock - connectedAt) / 1000)) : 0
  readonly property string elapsed: String(Math.floor(seconds / 60)).padStart(2, "0") + ":" + String(seconds % 60).padStart(2, "0")
  implicitHeight: contents.implicitHeight + Style.space(14)
  color: Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.085)
  border.color: Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.16)
  radius: Style.cornerRadius

  Timer { interval: 1000; repeat: true; running: root.visible && root.active && root.connectedAt > 0; onTriggered: root.clock = Date.now() }

  ColumnLayout {
    id: contents
    anchors.fill: parent
    anchors.margins: Style.space(7)
    spacing: Style.space(6)

    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(6)

      Text {
        Layout.fillWidth: true
        text: root.contact || root.number || "Pixel call"
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }

      Text {
        textFormat: Text.PlainText
        visible: root.active && root.connectedAt > 0
        text: root.elapsed
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
        Accessible.name: "Call duration " + text
      }
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(6)

      Button {
        id: muteButton
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        Layout.minimumWidth: 0
        Layout.preferredHeight: Style.space(38)
        iconText: root.muted ? "\u{f036d}" : "\u{f036c}"
        tooltipText: root.muted ? "Unmute microphone" : "Mute microphone"
        Accessible.name: tooltipText
        bordered: true
        focusable: true
        selected: root.muted
        enabled: root.audioConnected
        onClicked: root.muteRequested()
      }

      Button {
        id: shareButton
        Layout.fillWidth: true
        Layout.preferredWidth: 4
        Layout.minimumWidth: 0
        Layout.preferredHeight: Style.space(38)
        horizontalPadding: Style.space(4)
        fontSize: Style.font.bodySmall
        text: root.sharing ? "Stop sharing" : "Share computer audio"
        bordered: true
        focusable: true
        selected: root.sharing
        enabled: root.audioConnected || root.sharing
        onClicked: root.shareRequested()
      }

      Button {
        id: endButton
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        Layout.minimumWidth: 0
        Layout.preferredHeight: Style.space(38)
        iconText: "\u{f03f5}"
        tooltipText: root.canHangUp ? "Hang up" : "Bluetooth hang-up unavailable"
        Accessible.name: tooltipText
        bordered: true
        focusable: true
        foreground: Color.urgent
        background: Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.14)
        enabled: root.canHangUp && root.state !== "Ending call…"
        onClicked: root.hangupRequested()
      }
    }
  }
}
