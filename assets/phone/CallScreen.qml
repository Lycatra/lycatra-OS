import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

Rectangle {
  id: root
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
  signal openPhoneRequested()
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

    Text {
      textFormat: Text.PlainText
      Layout.alignment: Qt.AlignHCenter
      visible: root.active && root.connectedAt > 0
      text: root.elapsed
      color: Color.foreground
      font.family: Style.font.family
      font.pixelSize: Style.font.body
      font.bold: true
      Accessible.name: "Call duration " + text
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(6)

      Button {
        Layout.preferredWidth: Style.space(52)
        Layout.fillHeight: true
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
        Layout.fillWidth: true
        text: root.sharing ? "Stop sharing" : "Share computer audio"
        bordered: true
        focusable: true
        selected: root.sharing
        enabled: root.audioConnected || root.sharing
        onClicked: root.shareRequested()
      }

      Button {
        Layout.preferredWidth: Style.space(52)
        Layout.fillHeight: true
        iconText: root.canHangUp ? "\u{f03f5}" : "\u{f03f2}"
        tooltipText: root.canHangUp ? "Hang up" : "Open phone to end the call"
        Accessible.name: tooltipText
        bordered: true
        focusable: true
        foreground: root.canHangUp ? Color.urgent : Color.foreground
        enabled: root.state !== "Ending call…"
        onClicked: {
          if (root.canHangUp) root.hangupRequested()
          else root.openPhoneRequested()
        }
      }
    }
  }
}
