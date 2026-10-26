import Foundation
import CoreBluetooth

final class Scanner: NSObject, CBCentralManagerDelegate {
    var manager: CBCentralManager!
    var seen: [String: [String: Any]] = [:]
    var started = false

    func start() {
        manager = CBCentralManager(delegate: self, queue: nil)
    }

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        fputs("Bluetooth state: \(central.state.rawValue), authorization: \(CBManager.authorization.rawValue)\n", stderr)
        if central.state == .poweredOn && !started {
            started = true
            central.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: true])
            DispatchQueue.main.asyncAfter(deadline: .now() + 30) { self.finish() }
        } else if [.unauthorized, .unsupported, .poweredOff].contains(central.state) {
            finish(code: 1)
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let name = advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? peripheral.name ?? ""
        guard name.hasPrefix("JOMOO_SMART") else { return }
        let id = peripheral.identifier.uuidString
        var item: [String: Any] = ["id": id, "name": advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? peripheral.name ?? "(unnamed)", "rssi": RSSI]
        item["services"] = (advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? []).map { $0.uuidString }
        if let data = advertisementData[CBAdvertisementDataManufacturerDataKey] as? Data {
            item["manufacturer_hex"] = data.map { String(format: "%02x", $0) }.joined()
        }
        if let data = advertisementData[CBAdvertisementDataServiceDataKey] as? [CBUUID: Data] {
            item["service_data"] = Dictionary(uniqueKeysWithValues: data.map { ($0.key.uuidString, $0.value.map { String(format: "%02x", $0) }.joined()) })
        }
        seen[id] = item
    }

    func finish(code: Int32 = 0) {
        if started { manager.stopScan() }
        let entries = seen.values.sorted { ($0["rssi"] as? Int ?? -999) > ($1["rssi"] as? Int ?? -999) }
        if let data = try? JSONSerialization.data(withJSONObject: entries, options: [.prettyPrinted, .sortedKeys]), let result = String(data: data, encoding: .utf8) { print(result) }
        exit(code)
    }
}

let scanner = Scanner()
scanner.start()
DispatchQueue.main.asyncAfter(deadline: .now() + 45) { scanner.finish(code: 1) }
RunLoop.main.run()
