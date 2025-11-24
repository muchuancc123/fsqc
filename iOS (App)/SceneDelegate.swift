//
//  SceneDelegate.swift
//  iOS (App)
//
//  Created by 阿祖 on 2025/11/13.
//

import UIKit
import SwiftUI
import Combine
import CryptoKit

class SceneDelegate: UIResponder, UIWindowSceneDelegate {

    var window: UIWindow?

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let windowScene = scene as? UIWindowScene else { return }
        let window = UIWindow(windowScene: windowScene)
        window.rootViewController = UIHostingController(rootView: RootView().environmentObject(AppState()))
        self.window = window
        window.makeKeyAndVisible()
    }

}

enum UserRole: String, Codable, CaseIterable {
    case superAdmin
    case admin
    case operatorRole
}

struct User: Identifiable, Hashable {
    let id: UUID
    var username: String
    var displayName: String
    var role: UserRole
    var parentID: UUID?
    var isActive: Bool
    var passwordSalt: String
    var passwordHash: String
}

struct Channel: Identifiable, Hashable {
    let id: UUID
    var name: String
    var createdBy: UUID
    var ownerAdmin: UUID
    var isActive: Bool
    var createdAt: Date
}

struct Customer: Identifiable, Hashable {
    let id: UUID
    var phoneRaw: String
    var phoneNormalized: String
    var phoneHash: String
    var phoneEncrypted: String
    var channelID: UUID
    var ownerOperatorID: UUID
    var ownerAdminID: UUID
    var createdAt: Date
    var extraInfo: [String: String]
}

struct DuplicateRecord: Identifiable, Hashable {
    let id: UUID
    var customerID: UUID
    var firstOwnerID: UUID
    var duplicateOperatorID: UUID
    var duplicateChannelID: UUID
    var duplicateAt: Date
}

enum StatsRange: String, CaseIterable {
    case daily
    case weekly
    case monthly
}

struct StatsSummary {
    var totalInput: Int
    var duplicateCnt: Int
    var validCnt: Int
}

final class CryptoService {
    private let key: SymmetricKey
    init() {
        self.key = SymmetricKey(size: .bits256)
    }
    func sha256Hex(_ text: String) -> String {
        let data = Data(text.utf8)
        let digest = SHA256.hash(data: data)
        return digest.compactMap { String(format: "%02x", $0) }.joined()
    }
    func encrypt(_ text: String) -> String? {
        let data = Data(text.utf8)
        do {
            let sealed = try AES.GCM.seal(data, using: key)
            let combined = sealed.combined ?? Data()
            return combined.base64EncodedString()
        } catch {
            return nil
        }
    }
    func decrypt(_ base64: String) -> String? {
        guard let combined = Data(base64Encoded: base64) else { return nil }
        do {
            let box = try AES.GCM.SealedBox(combined: combined)
            let decrypted = try AES.GCM.open(box, using: key)
            return String(data: decrypted, encoding: .utf8)
        } catch {
            return nil
        }
    }
}

struct PhoneNormalizer {
    static func normalize(_ input: String, defaultCountryCode: String = "+1") throws -> String {
        var s = input.trimmingCharacters(in: .whitespacesAndNewlines)
        let removeSet = CharacterSet(charactersIn: " +()-－—")
        s = s.components(separatedBy: removeSet).joined()
        var result = ""
        for ch in s {
            if ch.isNumber { result.append(ch) }
        }
        let regex = try NSRegularExpression(pattern: "^[0-9]{4,11}$")
        let range = NSRange(location: 0, length: result.utf16.count)
        if regex.firstMatch(in: result, options: [], range: range) == nil {
            throw NSError(domain: "Phone", code: 1, userInfo: [NSLocalizedDescriptionKey: "invalid"])
        }
        return result
    }
}

final class AppState: ObservableObject {
    @Published var currentUser: User?
    @Published var users: [User] = []
    @Published var channels: [Channel] = []
    @Published var customers: [Customer] = []
    @Published var duplicates: [DuplicateRecord] = []
    private let crypto = CryptoService()

    init() {
        seed()
    }

    func seed() {
        let superAdmin = User(id: UUID(), username: "super", displayName: "超级管理员", role: .superAdmin, parentID: nil, isActive: true, passwordSalt: "s1", passwordHash: crypto.sha256Hex("123456s1"))
        let adminA = User(id: UUID(), username: "adminA", displayName: "管理员A", role: .admin, parentID: superAdmin.id, isActive: true, passwordSalt: "s2", passwordHash: crypto.sha256Hex("123456s2"))
        let opAlice = User(id: UUID(), username: "opAlice", displayName: "运营Alice", role: .operatorRole, parentID: adminA.id, isActive: true, passwordSalt: "s3", passwordHash: crypto.sha256Hex("123456s3"))
        users = [superAdmin, adminA, opAlice]
        channels = [
            Channel(id: UUID(), name: "Facebook-美区A", createdBy: superAdmin.id, ownerAdmin: adminA.id, isActive: true, createdAt: Date()),
            Channel(id: UUID(), name: "Google-美区B", createdBy: superAdmin.id, ownerAdmin: adminA.id, isActive: true, createdAt: Date())
        ]
    }

    func login(username: String, password: String) -> Bool {
        guard let user = users.first(where: { $0.username == username && $0.isActive }) else { return false }
        let hash = crypto.sha256Hex(password + user.passwordSalt)
        if hash == user.passwordHash {
            currentUser = user
            return true
        }
        return false
    }

    func logout() {
        currentUser = nil
    }

    func allowedChannels(for user: User) -> [Channel] {
        switch user.role {
        case .superAdmin:
            return channels.filter { $0.isActive }
        case .admin:
            return channels.filter { $0.ownerAdmin == user.id && $0.isActive }
        case .operatorRole:
            guard let adminID = user.parentID else { return [] }
            return channels.filter { $0.ownerAdmin == adminID && $0.isActive }
        }
    }

    struct CustomerResult {
        enum Status { case success, duplicate }
        var status: Status
        var existingOwner: User?
        var existingCreatedAt: Date?
    }

    func createCustomer(phoneRaw: String, channelID: UUID, operatorID: UUID) throws -> CustomerResult {
        let operatorUser = users.first(where: { $0.id == operatorID && $0.role == .operatorRole })
        guard let op = operatorUser, let adminID = op.parentID else { throw NSError(domain: "Auth", code: 1) }
        let normalized = try PhoneNormalizer.normalize(phoneRaw)
        let phoneHash = crypto.sha256Hex(normalized)
        let encrypted = crypto.encrypt(normalized) ?? ""
        if let existing = customers.first(where: { $0.phoneHash == phoneHash && $0.ownerAdminID == adminID }) {
            let dup = DuplicateRecord(id: UUID(), customerID: existing.id, firstOwnerID: existing.ownerOperatorID, duplicateOperatorID: op.id, duplicateChannelID: channelID, duplicateAt: Date())
            duplicates.append(dup)
            let owner = users.first(where: { $0.id == existing.ownerOperatorID })
            return CustomerResult(status: .duplicate, existingOwner: owner, existingCreatedAt: existing.createdAt)
        } else {
            let new = Customer(id: UUID(), phoneRaw: phoneRaw, phoneNormalized: normalized, phoneHash: phoneHash, phoneEncrypted: encrypted, channelID: channelID, ownerOperatorID: op.id, ownerAdminID: adminID, createdAt: Date(), extraInfo: [:])
            customers.append(new)
            return CustomerResult(status: .success, existingOwner: nil, existingCreatedAt: nil)
        }
    }

    func customersFor(user: User) -> [Customer] {
        switch user.role {
        case .superAdmin:
            return customers
        case .admin:
            return customers.filter { $0.ownerAdminID == user.id }
        case .operatorRole:
            return customers.filter { $0.ownerOperatorID == user.id }
        }
    }

    func duplicatesFor(customerID: UUID) -> [DuplicateRecord] {
        return duplicates.filter { $0.customerID == customerID }
    }

    func duplicateCountFor(ownerOperatorID: UUID) -> Int {
        return duplicates.filter { $0.firstOwnerID == ownerOperatorID }.count
    }

    func stats(for user: User, range: StatsRange) -> StatsSummary {
        let cal = Calendar.current
        let now = Date()
        let dateFilter: (Date) -> Bool = { d in
            switch range {
            case .daily:
                return cal.isDate(d, inSameDayAs: now)
            case .weekly:
                return cal.component(.weekOfYear, from: d) == cal.component(.weekOfYear, from: now) && cal.component(.yearForWeekOfYear, from: d) == cal.component(.yearForWeekOfYear, from: now)
            case .monthly:
                return cal.component(.month, from: d) == cal.component(.month, from: now) && cal.component(.year, from: d) == cal.component(.year, from: now)
            }
        }
        let scopedCustomers = customersFor(user: user).filter { dateFilter($0.createdAt) }
        let scopedDuplicates: [DuplicateRecord]
        switch user.role {
        case .superAdmin:
            scopedDuplicates = duplicates.filter { dateFilter($0.duplicateAt) }
        case .admin:
            let adminID = user.id
            let ownerOps = users.filter { $0.role == .operatorRole && $0.parentID == adminID }.map { $0.id }
            let setOps = Set(ownerOps)
            scopedDuplicates = duplicates.filter { dateFilter($0.duplicateAt) && setOps.contains($0.firstOwnerID) }
        case .operatorRole:
            scopedDuplicates = duplicates.filter { dateFilter($0.duplicateAt) && $0.firstOwnerID == user.id }
        }
        let totalInput = scopedCustomers.count + scopedDuplicates.count
        let duplicateCnt = scopedDuplicates.count
        var set = Set<String>()
        for c in scopedCustomers { set.insert(c.phoneHash) }
        let validCnt = set.count
        return StatsSummary(totalInput: totalInput, duplicateCnt: duplicateCnt, validCnt: validCnt)
    }
}

struct RootView: View {
    @EnvironmentObject var app: AppState
    var body: some View {
        Group {
            if let user = app.currentUser {
                DashboardView(user: user)
            } else {
                LoginView()
            }
        }
    }
}

struct LoginView: View {
    @EnvironmentObject var app: AppState
    @State var username: String = ""
    @State var password: String = ""
    @State var error: String?
    var body: some View {
        VStack(spacing: 16) {
            Text("重粉管理后台").font(.title).bold()
            VStack(alignment: .leading, spacing: 8) {
                TextField("用户名", text: $username)
                    .textFieldStyle(RoundedBorderTextFieldStyle())
                SecureField("密码", text: $password)
                    .textFieldStyle(RoundedBorderTextFieldStyle())
            }
            if let e = error { Text(e).foregroundColor(.red) }
            Button("登录") {
                if app.login(username: username, password: password) { error = nil } else { error = "用户名或密码错误" }
            }
            .buttonStyle(.borderedProminent)
            .padding(.top, 8)
        }
        .padding(24)
    }
}

struct DashboardView: View {
    @EnvironmentObject var app: AppState
    let user: User
    var body: some View {
        VStack {
            HStack {
                Text(user.displayName).font(.headline)
                Spacer()
                Button("退出") { app.logout() }
            }
            .padding([.top, .horizontal])
            switch user.role {
            case .operatorRole:
                OperatorHome(user: user)
            case .admin:
                AdminHome(user: user)
            case .superAdmin:
                SuperAdminHome(user: user)
            }
        }
    }
}

struct OperatorHome: View {
    @EnvironmentObject var app: AppState
    let user: User
    @State var range: StatsRange = .daily
    var body: some View {
        VStack(spacing: 12) {
            StatsHeader(user: user, range: $range)
            CustomerInputView(operatorUser: user)
            CustomerListView(scopeUser: user)
        }
        .padding(.horizontal)
    }
}

struct AdminHome: View {
    @EnvironmentObject var app: AppState
    let user: User
    @State var range: StatsRange = .daily
    var body: some View {
        VStack(spacing: 12) {
            StatsHeader(user: user, range: $range)
            ChannelsListView(ownerAdmin: user)
            CustomerListView(scopeUser: user)
        }
        .padding(.horizontal)
    }
}

struct SuperAdminHome: View {
    @EnvironmentObject var app: AppState
    let user: User
    @State var range: StatsRange = .daily
    var body: some View {
        VStack(spacing: 12) {
            StatsHeader(user: user, range: $range)
            CustomerListView(scopeUser: user)
        }
        .padding(.horizontal)
    }
}

struct StatsHeader: View {
    @EnvironmentObject var app: AppState
    let user: User
    @Binding var range: StatsRange
    var body: some View {
        let s = app.stats(for: user, range: range)
        HStack(spacing: 16) {
            Picker("", selection: $range) {
                ForEach(StatsRange.allCases, id: \.self) { r in
                    Text(r.rawValue)
                }
            }
            .pickerStyle(SegmentedPickerStyle())
            Spacer()
            HStack(spacing: 24) {
                MetricView(title: "录入", value: s.totalInput)
                MetricView(title: "重复", value: s.duplicateCnt)
                MetricView(title: "有效", value: s.validCnt)
            }
        }
        .padding(.vertical)
    }
}

struct MetricView: View {
    let title: String
    let value: Int
    var body: some View {
        VStack {
            Text(title).font(.caption).foregroundColor(.secondary)
            Text("\(value)").font(.headline)
        }
    }
}

struct CustomerInputView: View {
    @EnvironmentObject var app: AppState
    let operatorUser: User
    @State var phone: String = ""
    @State var selectedChannelID: UUID?
    @State var alertMessage: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("录入客户").font(.headline)
            Picker("渠道", selection: Binding(get: { selectedChannelID ?? app.allowedChannels(for: operatorUser).first?.id }, set: { selectedChannelID = $0 })) {
                ForEach(app.allowedChannels(for: operatorUser)) { ch in
                    Text(ch.name).tag(ch.id as UUID?)
                }
            }
            .pickerStyle(MenuPickerStyle())
            TextField("手机号", text: $phone)
                .textFieldStyle(RoundedBorderTextFieldStyle())
            Button("提交") {
                guard let chID = selectedChannelID ?? app.allowedChannels(for: operatorUser).first?.id else { return }
                do {
                    let res = try app.createCustomer(phoneRaw: phone, channelID: chID, operatorID: operatorUser.id)
                    switch res.status {
                    case .success:
                        alertMessage = "录入成功"
                        phone = ""
                    case .duplicate:
                        let name = res.existingOwner?.displayName ?? "未知"
                        let date = res.existingCreatedAt ?? Date()
                        let fmt = DateFormatter()
                        fmt.dateFormat = "yyyy-MM-dd HH:mm"
                        alertMessage = "\(name)已经拥有了该客户，于 \(fmt.string(from: date)) 录入"
                    }
                } catch {
                    alertMessage = "手机号格式不合法"
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding()
        .background(RoundedRectangle(cornerRadius: 8).stroke(Color.gray.opacity(0.2)))
        .alert(item: Binding(get: {
            alertMessage.map { AlertMessage(text: $0) }
        }, set: { _ in alertMessage = nil })) { msg in
            Alert(title: Text(msg.text))
        }
    }
}

struct AlertMessage: Identifiable { let id = UUID(); let text: String }

struct CustomerListView: View {
    @EnvironmentObject var app: AppState
    let scopeUser: User
    @State var selectedCustomer: Customer?
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("客户列表").font(.headline)
            List(app.customersFor(user: scopeUser)) { c in
                HStack {
                    Text(c.phoneNormalized)
                    Spacer()
                    Text(channelName(for: c.channelID))
                    Spacer()
                    Text(dateString(c.createdAt)).foregroundColor(.secondary)
                    Spacer()
                    Text("重复 \(app.duplicates.filter { $0.customerID == c.id }.count)")
                }
                .contentShape(Rectangle())
                .onTapGesture { selectedCustomer = c }
            }
        }
        .sheet(item: $selectedCustomer) { cust in
            CustomerDetailView(customer: cust)
        }
        .padding(.top)
    }
    func channelName(for id: UUID) -> String {
        app.channels.first(where: { $0.id == id })?.name ?? ""
    }
    func dateString(_ d: Date) -> String {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm"; return f.string(from: d)
    }
}

struct CustomerDetailView: View {
    @EnvironmentObject var app: AppState
    let customer: Customer
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("客户详情").font(.headline)
            HStack {
                Text("手机号")
                Spacer()
                Text(customer.phoneNormalized)
            }
            HStack {
                Text("渠道")
                Spacer()
                Text(app.channels.first(where: { $0.id == customer.channelID })?.name ?? "")
            }
            Text("被重复记录").font(.headline)
            List(app.duplicatesFor(customerID: customer.id)) { d in
                VStack(alignment: .leading) {
                    Text(dateString(d.duplicateAt))
                    Text("重复者: \(app.users.first(where: { $0.id == d.duplicateOperatorID })?.displayName ?? "")")
                    Text("渠道: \(app.channels.first(where: { $0.id == d.duplicateChannelID })?.name ?? "")")
                }
            }
        }
        .padding()
    }
    func dateString(_ d: Date) -> String {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm"; return f.string(from: d)
    }
}

struct ChannelsListView: View {
    @EnvironmentObject var app: AppState
    let ownerAdmin: User
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("渠道").font(.headline)
            List(app.allowedChannels(for: ownerAdmin)) { ch in
                HStack { Text(ch.name); Spacer(); Text(dateString(ch.createdAt)).foregroundColor(.secondary) }
            }
        }
    }
    func dateString(_ d: Date) -> String { let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; return f.string(from: d) }
}
