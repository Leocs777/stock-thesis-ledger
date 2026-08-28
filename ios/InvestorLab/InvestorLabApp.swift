import Charts
import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers
import UserNotifications

@main
struct InvestorLabApp: App {
    @StateObject private var store = LabStore()
    @AppStorage("appLanguage") private var appLanguage = "zh-Hans"

    var body: some Scene {
        WindowGroup {
            RootView(store: store)
                .environment(\.locale, Locale(identifier: appLanguage))
        }
    }
}

private enum AppTab: Hashable {
    case lab
    case command
    case journal
    case settings
}

private struct RootView: View {
    @ObservedObject var store: LabStore
    @State private var selectedTab = AppTab.lab

    var body: some View {
        Group {
            switch store.authState {
            case .checking:
                ZStack {
                    Color.labInk.ignoresSafeArea()
                    ProgressView("Opening private ledger…").tint(.signalOrange).foregroundStyle(.white)
                }
            case .signedOut:
                AuthenticationView(store: store)
            case .signedIn:
                TabView(selection: $selectedTab) {
                    DashboardView(
                        store: store,
                        openCommand: { selectedTab = .command },
                        openJournal: { selectedTab = .journal }
                    )
                        .tabItem { Label("Lab", systemImage: "rectangle.3.group.fill") }
                        .tag(AppTab.lab)
                    CommandCenterView(store: store)
                        .tabItem { Label("Command", systemImage: "scope") }
                        .tag(AppTab.command)
                    JournalView(store: store)
                        .tabItem { Label("Journal", systemImage: "clock.arrow.circlepath") }
                        .tag(AppTab.journal)
                    SettingsView(store: store)
                        .tabItem { Label("Settings", systemImage: "slider.horizontal.3") }
                        .tag(AppTab.settings)
                }
                .tint(.signalOrange)
            }
        }
        .preferredColorScheme(.light)
        .task { await store.bootstrap() }
        .alert("Request failed", isPresented: errorBinding) {
            Button("OK", role: .cancel) { store.errorMessage = nil }
        } message: {
            Text(labLocalized(store.errorMessage ?? "Unknown error"))
        }
    }

    private var errorBinding: Binding<Bool> {
        Binding(
            get: { store.errorMessage != nil },
            set: { if !$0 { store.errorMessage = nil } }
        )
    }
}

private enum AuthMode: String, CaseIterable, Identifiable {
    case login = "Sign in"
    case register = "Create vault"

    var id: Self { self }
}

private struct AuthenticationView: View {
    @ObservedObject var store: LabStore
    @State private var mode = AuthMode.register
    @State private var displayName = ""
    @State private var email = ""
    @State private var password = ""

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [.labInk, .labInkSoft],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()
            Circle()
                .fill(Color.signalOrange.opacity(0.30))
                .frame(width: 330, height: 330)
                .blur(radius: 60)
                .offset(x: -170, y: -340)
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    HStack(spacing: 11) {
                        Image("AppLogo")
                            .resizable()
                            .scaledToFit()
                            .frame(width: 36, height: 36)
                            .clipShape(RoundedRectangle(cornerRadius: 11))
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Investor Lab").font(.headline).foregroundStyle(.white)
                            Text("LOCAL ACCOUNT LAYER")
                                .font(.system(size: 11, weight: .bold))
                                .tracking(1.1)
                                .foregroundStyle(Color.white.opacity(0.72))
                        }
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        Text("PRIVATE BY ARCHITECTURE")
                            .font(.caption.weight(.bold))
                            .tracking(1.2)
                            .foregroundStyle(Color.signalOrange)
                        Text("Your ledger.\nYour machine.")
                            .font(.system(size: 48, weight: .semibold))
                            .tracking(-1.5)
                            .foregroundStyle(.white)
                        Text("One private account keeps the web terminal and iPhone on the same append-only research history.")
                            .font(.subheadline)
                            .foregroundStyle(Color.white.opacity(0.78))
                    }

                    VStack(spacing: 15) {
                        Picker("Account action", selection: $mode) {
                            ForEach(AuthMode.allCases) { Text(LocalizedStringKey($0.rawValue)).tag($0) }
                        }
                        .pickerStyle(.segmented)

                        if mode == .register {
                            TextField("Display name", text: $displayName)
                                .textContentType(.name)
                                .textInputAutocapitalization(.words)
                                .labAuthField()
                        }
                        TextField("Email", text: $email)
                            .textContentType(.emailAddress)
                            .keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .labAuthField()
                        SecureField("Password", text: $password)
                            .textContentType(mode == .register ? .newPassword : .password)
                            .labAuthField()

                        Button {
                            Task {
                                let succeeded: Bool
                                if mode == .register {
                                    succeeded = await store.register(
                                        displayName: displayName, email: email, password: password
                                    )
                                } else {
                                    succeeded = await store.login(email: email, password: password)
                                }
                                if succeeded { password = "" }
                            }
                        } label: {
                            Text(LocalizedStringKey(mode == .register ? "Create local account" : "Sign in"))
                        }
                        .buttonStyle(LabButtonStyle())
                        .disabled(
                            store.isLoading || email.isEmpty || password.isEmpty
                                || (mode == .register && displayName.isEmpty)
                        )

                        Divider()
                        VStack(alignment: .leading, spacing: 7) {
                            Text("LOCAL SERVER").font(.system(size: 11, weight: .bold)).tracking(0.9)
                            TextField("http://127.0.0.1:8000", text: $store.serverURL)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                                .keyboardType(.URL)
                                .labAuthField()
                        }
                        Text("Use 12–128 characters with an uppercase letter and a number. The session token stays in this iPhone's Keychain.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(22)
                    .background(Color.labCard, in: RoundedRectangle(cornerRadius: 25, style: .continuous))
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 28)
            }
        }
    }
}

private extension View {
    func labAuthField() -> some View {
        self
            .padding(.horizontal, 13)
            .frame(minHeight: 46)
            .background(Color.white, in: RoundedRectangle(cornerRadius: LabTokens.Radius.small))
            .overlay(RoundedRectangle(cornerRadius: LabTokens.Radius.small).stroke(Color.labLine))
    }
}

private enum ResearchMode: String, CaseIterable, Identifiable {
    case invest = "Invest"
    case dayTrade = "Day"
    case options = "Options"

    var id: Self { self }
}

private struct WorkspaceJump: Identifiable {
    let id: String
    let title: String
}

private struct DashboardView: View {
    @ObservedObject var store: LabStore
    let openCommand: () -> Void
    let openJournal: () -> Void
    @Environment(\.locale) private var locale
    @AppStorage("workflowSymbol") private var workflowSymbol = ""
    @State private var mode = ResearchMode.invest
    @State private var showingWatchlistForm = false
    @State private var showingTradeForm = false
    @State private var showingAlertForm = false
    @State private var researchSymbol = ""
    @State private var companySearchQuery = ""
    @State private var screenerSegment = "all"
    @State private var strategyStyle = "balanced"
    @State private var timeHorizon = "swing"
    @State private var templateName = ""
    @State private var technicalWeight = "50"
    @State private var fundamentalWeight = "25"
    @State private var valuationWeight = "10"
    @State private var portfolioWeight = "15"
    @State private var strategyCost = "10"
    @State private var rebalanceTargets = ""

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        header
                        Picker("Workspace", selection: $mode) {
                            ForEach(ResearchMode.allCases) { item in
                                Text(LocalizedStringKey(item.rawValue)).tag(item)
                            }
                        }
                        .pickerStyle(.segmented)
                        .accessibilityLabel("Research workspace")
                        workspaceJumpBar(proxy)

                        switch mode {
                        case .invest:
                            investWorkspace
                        case .dayTrade:
                            DayTradeWorkspace(store: store)
                        case .options:
                            OptionsWorkspace(store: store)
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 18)
                }
                .background(Color.labPaper.ignoresSafeArea())
                .refreshable { await store.load() }
                .navigationTitle("Investor Lab")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Menu {
                            Button("Add to watchlist", systemImage: "star") {
                                showingWatchlistForm = true
                            }
                            Button("Record paper trade", systemImage: "arrow.left.arrow.right") {
                                showingTradeForm = true
                            }
                            Button("Add price alert", systemImage: "bell") {
                                showingAlertForm = true
                            }
                        } label: {
                            Image(systemName: "plus.circle.fill")
                        }
                    }
                }
                .sheet(isPresented: $showingWatchlistForm) {
                    AddWatchlistView(store: store)
                }
                .sheet(isPresented: $showingTradeForm) {
                    PaperTradeView(store: store)
                }
                .sheet(isPresented: $showingAlertForm) {
                    AddPriceAlertView(store: store)
                }
                .onAppear { loadStrategyProfile() }
                .onChange(of: store.snapshot.investorProfile.updatedAt) { _, _ in
                    loadStrategyProfile()
                }
            }
        }
    }

    private var workspaceJumps: [WorkspaceJump] {
        switch mode {
        case .invest:
            return [
                WorkspaceJump(id: "invest-workflow", title: "Guided workflow"),
                WorkspaceJump(id: "invest-strategy", title: "Trading strategy"),
                WorkspaceJump(id: "invest-performance", title: "Paper portfolio performance"),
                WorkspaceJump(id: "invest-watchlist", title: "Watchlist"),
                WorkspaceJump(id: "invest-market", title: "Market evidence"),
                WorkspaceJump(id: "invest-decision", title: "Decision analysis"),
            ]
        case .dayTrade:
            return [
                WorkspaceJump(id: "day-scanner", title: "Watchlist scanner"),
                WorkspaceJump(id: "day-live", title: "Live market plan"),
                WorkspaceJump(id: "day-guardrails", title: "Risk guardrails"),
                WorkspaceJump(id: "day-worksheet", title: "Risk worksheet"),
            ]
        case .options:
            return [
                WorkspaceJump(id: "options-chain", title: "Option chain"),
                WorkspaceJump(id: "options-payoff", title: "Payoff worksheet"),
            ]
        }
    }

    private func workspaceJumpBar(_ proxy: ScrollViewProxy) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(workspaceJumps) { jump in
                    Button(labLocalized(jump.title)) {
                        withAnimation(.easeInOut(duration: 0.25)) { proxy.scrollTo(jump.id, anchor: .top) }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }
            }
        }
        .accessibilityLabel("Section shortcuts")
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("DAILY RESEARCH BRIEF")
                    .font(.caption.weight(.bold))
                    .tracking(1.3)
                    .foregroundStyle(Color.signalOrange)
                Spacer()
                Label(store.isLoading ? "Syncing" : "Local / synced", systemImage: "circle.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(store.isLoading ? Color.white.opacity(0.75) : Color.labMint)
            }
            Text("Capital before conviction.")
                .font(.system(size: 37, weight: .semibold))
                .tracking(-1.1)
                .foregroundStyle(.white)
            Text("Track position size, cost basis, and the evidence behind each end-of-day decision.")
                .font(.subheadline)
                .foregroundStyle(Color.white.opacity(0.78))
                .fixedSize(horizontal: false, vertical: true)
            Divider().overlay(Color.white.opacity(0.14))
            HStack(spacing: 0) {
                HeaderMetric(label: "Positions", value: "\(store.openPositionCount)")
                HeaderMetric(label: "Watchlist", value: "\(store.snapshot.watchlist.count)")
                HeaderMetric(
                    label: "Realized",
                    value: store.snapshot.portfolio.realizedPNL.currency,
                    color: store.snapshot.portfolio.realizedPNL.decimal < 0 ? .red : .labMint
                )
            }
        }
        .padding(22)
        .background(
            LinearGradient(colors: [.labInk, .labInkSoft], startPoint: .topLeading, endPoint: .bottomTrailing),
            in: RoundedRectangle(cornerRadius: 27, style: .continuous)
        )
        .overlay(alignment: .topTrailing) {
            Circle()
                .fill(Color.signalOrange.opacity(0.26))
                .frame(width: 170, height: 170)
                .blur(radius: 30)
                .offset(x: 60, y: -70)
                .allowsHitTesting(false)
        }
        .clipShape(RoundedRectangle(cornerRadius: 27, style: .continuous))
    }

    @ViewBuilder private var investWorkspace: some View {
        HStack(spacing: 10) {
            LabMetricCard(label: "Execution", value: "Paper", detail: "Gated in Command")
            LabMetricCard(
                label: "Market data",
                value: store.marketStatus?.configured == true ? "EOD" : "Off",
                detail: store.marketStatus?.configured == true ? "Alpha Vantage" : "API key required"
            )
        }
        workflowGuide.id("invest-workflow")
        strategyLens.id("invest-strategy")
        dailyBriefing
        portfolioPerformance.id("invest-performance")
        paperAccountMirror
        watchlistScreener
        companySearch
        watchlist.id("invest-watchlist")
        marketEvidence.id("invest-market")
        fundamentalEvidence
        earningsCalendar
        secEventMonitor
        decisionCenter
        decisionDetail.id("invest-decision")
        positions
        portfolioRisk
        portfolioActions
        rebalanceCalculator
        priceAlerts
    }

    private var workflowGuide: some View {
        LabSection(
            title: "Guided workflow",
            subtitle: "Complete the core loop first. Advanced research tools stay available below.",
            badge: "4 STEPS"
        ) {
            workflowButton(number: "01", title: "Choose a stock", detail: "Add a ticker to the synchronized watchlist.") {
                showingWatchlistForm = true
            }
            workflowButton(number: "02", title: "Refresh & score", detail: "Update evidence and generate the current decision.") {
                let symbol = activeWorkflowSymbol
                guard !symbol.isEmpty else {
                    store.errorMessage = labLocalized("Choose a stock before refreshing evidence.")
                    return
                }
                researchSymbol = symbol
                workflowSymbol = symbol
                Task {
                    await store.refreshMarket(symbol)
                }
            }
            workflowButton(number: "03", title: "Prepare paper order", detail: "Carry the selected symbol into the Paper-only ticket.") {
                let symbol = activeWorkflowSymbol
                guard !symbol.isEmpty else {
                    store.errorMessage = labLocalized("Choose a stock before preparing a paper order.")
                    return
                }
                workflowSymbol = symbol
                openCommand()
            }
            workflowButton(number: "04", title: "Review outcome", detail: "Close the loop in the synchronized journal.") {
                openJournal()
            }
            HStack {
                Text("Selected symbol:").foregroundStyle(.secondary)
                Text(activeWorkflowSymbol.isEmpty ? labLocalized("None") : activeWorkflowSymbol)
                    .fontWeight(.semibold)
                Spacer()
                Text("Decision:").foregroundStyle(.secondary)
                Text(workflowDecisionText)
                    .fontWeight(.semibold)
            }
            .font(.caption)
        }
    }

    private func workflowButton(number: String, title: String, detail: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 13) {
                Text(number)
                    .font(.caption2.weight(.black))
                    .foregroundStyle(Color.signalOrange)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 3) {
                    Text(LocalizedStringKey(title)).font(.subheadline.weight(.semibold))
                    Text(LocalizedStringKey(detail)).font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "chevron.right").font(.caption.weight(.bold)).foregroundStyle(Color.signalOrange)
            }
            .padding(13)
            .background(Color.white, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.labLine))
        }
        .buttonStyle(.plain)
    }

    private var activeWorkflowSymbol: String {
        let candidates = [researchSymbol, workflowSymbol, store.snapshot.watchlist.first?.symbol ?? ""]
        return candidates.first { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }?
            .trimmingCharacters(in: .whitespacesAndNewlines).uppercased() ?? ""
    }

    private var workflowDecisionText: String {
        guard let decision = store.decisionBundle?.latest,
              decision.symbol.caseInsensitiveCompare(activeWorkflowSymbol) == .orderedSame else {
            return labLocalized("Not generated")
        }
        return decision.score.map { "\(labLocalized(decision.signalLabel)) · \($0)/100" }
            ?? labLocalized(decision.signalLabel)
    }

    private func openResearch(_ symbol: String) {
        let normalized = symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !normalized.isEmpty else { return }
        researchSymbol = normalized
        workflowSymbol = normalized
        Task { await store.loadMarket(normalized) }
    }

    private var paperAccountMirror: some View {
        LabSection(
            title: "Alpaca Paper account mirror",
            subtitle: "Read-only synchronized mirror; Paper orders live in Command.",
            badge: "READ ONLY"
        ) {
            Button("Synchronize paper account") { Task { await store.synchronizePaperAccount() } }
                .buttonStyle(.bordered)
                .disabled(store.isLoading || store.currentUser?.role != "owner")
            if let paper = store.snapshot.paperAccount, paper.available, let account = paper.account {
                HStack(spacing: 10) {
                    LabMetricCard(label: "Paper equity", value: (account.equity ?? "0").currency, detail: account.status ?? "unknown")
                    LabMetricCard(label: "Buying power", value: (account.buyingPower ?? "0").currency, detail: "\(paper.positions?.count ?? 0) positions")
                }
                ForEach((paper.positions ?? []).prefix(12)) { position in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(position.symbol).font(.subheadline.weight(.semibold))
                            Text("\(position.qty ?? "—") · \(labLocalized(position.side ?? "unknown")) · \((position.currentPrice ?? "0").currency)")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text((position.unrealizedPL ?? "0").currency)
                            .font(.caption.monospacedDigit())
                            .foregroundStyle((position.unrealizedPL ?? "0").decimal < 0 ? Color.red : Color.labGreen)
                    }
                }
                Text(labLocalized(paper.scope ?? "Read-only paper account mirror."))
                    .font(.caption2).foregroundStyle(.secondary)
            } else {
                LabEmptyLine(text: store.snapshot.paperAccount?.reason ?? "Synchronize after saving Alpaca credentials.")
            }
        }
    }

    private var companySearch: some View {
        LabSection(
            title: "Company search",
            subtitle: "Find US public companies by ticker or legal name.",
            badge: "SEC EDGAR"
        ) {
            HStack(spacing: 10) {
                TextField("Apple or AAPL", text: $companySearchQuery)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .padding(.horizontal, 12)
                    .frame(minHeight: 44)
                    .background(Color.white, in: RoundedRectangle(cornerRadius: 11))
                    .overlay(RoundedRectangle(cornerRadius: 11).stroke(Color.labLine))
                Button("Search") { Task { await store.searchCompanies(companySearchQuery) } }
                    .buttonStyle(LabButtonStyle(compact: true))
                    .disabled(companySearchQuery.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            ForEach(store.companySearchResults) { item in
                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("\(item.symbol) · \(item.name)").font(.subheadline.weight(.semibold))
                        Text("\(labLocalized(item.match)) · CIK \(item.cik)")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Open") {
                        openResearch(item.symbol)
                    }
                    .buttonStyle(.bordered)
                    Button("Add") { Task { _ = await store.addSymbol(item.symbol) } }
                        .buttonStyle(.bordered)
                }
                .padding(.vertical, 4)
            }
        }
    }

    private var strategyLens: some View {
        LabSection(
            title: "Trading strategy",
            subtitle: "Choose the research lens saved with new decisions.",
            badge: "PROFILE"
        ) {
            Picker("Strategy style", selection: $strategyStyle) {
                Text("Balanced").tag("balanced")
                Text("Growth").tag("growth")
                Text("Value").tag("value")
                Text("Income").tag("income")
                Text("Momentum").tag("momentum")
            }
            Picker("Holding horizon", selection: $timeHorizon) {
                Text("Day").tag("day")
                Text("Swing").tag("swing")
                Text("Long term").tag("long_term")
            }
            VStack(alignment: .leading, spacing: 5) {
                Text("\(strategyName) · \(horizonName)")
                    .font(.subheadline.weight(.semibold))
                Text("\(strategyDescription) \(horizonDescription)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(strategyWeights)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.labPaper, in: RoundedRectangle(cornerRadius: 12))
            Button("Apply strategy") {
                let profile = store.snapshot.investorProfile
                Task {
                    _ = await store.updateInvestorProfile(
                        InvestorProfilePayload(
                            strategyStyle: strategyStyle,
                            timeHorizon: timeHorizon,
                            paperAccountSize: profile.paperAccountSize,
                            maxPositionPercent: profile.maxPositionPercent,
                            riskPerTradePercent: profile.riskPerTradePercent,
                            minimumRewardRisk: profile.minimumRewardRisk,
                            dailyLossLimit: profile.dailyLossLimit,
                            optionsDefinedRiskOnly: profile.optionsDefinedRiskOnly
                        )
                    )
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(Color.signalOrange)
            .disabled(store.isLoading)
            Divider()
            Text("CUSTOM STRATEGY TEMPLATE")
                .font(.caption2.weight(.bold))
                .foregroundStyle(.secondary)
            PlanningField("Template name", text: $templateName, keyboard: .default)
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                PlanningField("Technical %", text: $technicalWeight)
                PlanningField("Fundamentals %", text: $fundamentalWeight)
                PlanningField("Valuation %", text: $valuationWeight)
                PlanningField("Position risk %", text: $portfolioWeight)
                PlanningField("Cost bps / side", text: $strategyCost)
            }
            let weightTotal = [technicalWeight, fundamentalWeight, valuationWeight, portfolioWeight]
                .compactMap(Int.init).reduce(0, +)
            Text("Weights total \(weightTotal)%. \(weightTotal == 100 ? "Ready." : "Must equal 100%.")")
                .font(.caption)
                .foregroundStyle(weightTotal == 100 ? Color.labGreen : Color.red)
            Button("Save & activate template") {
                Task {
                    if await store.saveStrategyTemplate(
                        name: templateName,
                        technical: technicalWeight,
                        fundamental: fundamentalWeight,
                        valuation: valuationWeight,
                        portfolio: portfolioWeight,
                        costBps: strategyCost
                    ) { templateName = "" }
                }
            }
            .buttonStyle(LabButtonStyle())
            .disabled(templateName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || weightTotal != 100 || store.isLoading)
            ForEach(store.snapshot.strategyTemplates) { template in
                HStack(alignment: .top, spacing: 10) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(template.name).font(.subheadline.weight(.semibold))
                        Text("v\(template.versionNumber ?? 1) · \(template.technicalWeight)% technical · \(template.fundamentalWeight)% fundamentals · \(template.valuationWeight)% valuation · \(template.portfolioWeight)% risk · \(template.feeSlippageBps) bps")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if template.isActive {
                        Text("ACTIVE").font(.caption2.weight(.bold)).foregroundStyle(Color.labGreen)
                    } else {
                        Button("Activate") { Task { await store.activateStrategyTemplate(template.id) } }
                            .buttonStyle(.bordered)
                    }
                    Button(role: .destructive) { Task { await store.deleteStrategyTemplate(template.id) } } label: {
                        Image(systemName: "trash")
                    }
                }
                .padding(.vertical, 4)
            }
            if let versions = store.snapshot.strategyVersions, !versions.isEmpty {
                Divider()
                Text("IMMUTABLE VERSION HISTORY")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.secondary)
                ForEach(versions.prefix(12)) { version in
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("\(version.name) · v\(version.versionNumber)")
                                .font(.caption.weight(.semibold))
                            Text("\(version.config.technicalWeight)/\(version.config.fundamentalWeight)/\(version.config.valuationWeight)/\(version.config.portfolioWeight) · \(version.config.feeSlippageBps) bps · \(version.configHash.prefix(10))")
                                .font(.caption2.monospaced())
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        if version.activatedAt != nil {
                            Text("USED").font(.caption2.weight(.bold)).foregroundStyle(Color.labGreen)
                        }
                    }
                    .padding(.vertical, 3)
                }
            }
        }
    }

    private var strategyName: String {
        let english = ["balanced": "Balanced", "growth": "Growth", "value": "Value", "income": "Income", "momentum": "Momentum"]
        let chinese = ["balanced": "均衡", "growth": "成长", "value": "价值", "income": "收益", "momentum": "动量"]
        return (isChinese ? chinese : english)[strategyStyle] ?? (isChinese ? "均衡" : "Balanced")
    }

    private var horizonName: String {
        let english = ["day": "Day", "swing": "Swing", "long_term": "Long term"]
        let chinese = ["day": "日内", "swing": "波段", "long_term": "长期"]
        return (isChinese ? chinese : english)[timeHorizon] ?? (isChinese ? "波段" : "Swing")
    }

    private var strategyDescription: String {
        if isChinese {
            switch strategyStyle {
            case "growth": return "提高营收与净利润增长、盈利能力和趋势的权重。"
            case "value": return "重点评估市价/稀释 EPS、现金流、盈利能力和资产负债表。"
            case "income": return "重点评估申报股息率、自由现金流和资产负债表韧性。"
            case "momentum": return "重点评估趋势、20 日动量、成交量和波动率。"
            default: return "结合技术结构与 SEC 成长、盈利、现金流和资产负债表证据。"
            }
        }
        switch strategyStyle {
        case "growth": return "Raises the weight of revenue and income growth, profitability, and trend."
        case "value": return "Emphasizes price to diluted EPS, cash flow, profitability, and balance-sheet resilience."
        case "income": return "Emphasizes indicated dividend yield, free cash flow, and balance-sheet resilience."
        case "momentum": return "Emphasizes trend, 20-day momentum, volume, and volatility."
        default: return "Combines technical structure with SEC growth, profitability, cash flow, and balance-sheet evidence."
        }
    }

    private var strategyWeights: String {
        let base = [
            "balanced": (60, 25), "growth": (40, 45), "value": (35, 50),
            "income": (30, 55), "momentum": (75, 10),
        ][strategyStyle] ?? (60, 25)
        var technical = base.0
        var fundamentals = base.1
        if timeHorizon == "day" {
            let shift = min(10, fundamentals)
            technical += shift
            fundamentals -= shift
        } else if timeHorizon == "long_term" {
            let shift = min(10, technical)
            technical -= shift
            fundamentals += shift
        }
        return isChinese
            ? "\(technical)% 技术面 · \(fundamentals)% 基本面 · 15% 仓位适配"
            : "\(technical)% technical · \(fundamentals)% fundamentals · 15% position fit"
    }

    private var horizonDescription: String {
        if isChinese {
            switch timeHorizon {
            case "day": return "同一交易日的计划上下文。"
            case "long_term": return "数月级别的计划上下文。"
            default: return "数日至数周的计划上下文。"
            }
        }
        switch timeHorizon {
        case "day": return "Same-session planning context."
        case "long_term": return "Multi-month planning context."
        default: return "Multi-day to multi-week planning context."
        }
    }

    private var isChinese: Bool { locale.identifier.lowercased().hasPrefix("zh") }

    private func loadStrategyProfile() {
        strategyStyle = store.snapshot.investorProfile.strategyStyle
        timeHorizon = store.snapshot.investorProfile.timeHorizon
    }

    private var dailyBriefing: some View {
        let briefing = store.snapshot.dailyBriefing
        return LabSection(
            title: labLocalized(briefing.headline),
            subtitle: labLocalized(briefing.summary),
            badge: briefing.riskCount > 0 ? "RISK FIRST" : briefing.attentionCount > 0 ? "REVIEW" : "READY"
        ) {
            HStack(spacing: 10) {
                LabMetricCard(
                    label: "Needs attention",
                    value: "\(briefing.attentionCount)",
                    detail: "Data and workflow"
                )
                LabMetricCard(
                    label: "Candidates",
                    value: "\(briefing.opportunityCount)",
                    detail: "Current decision runs"
                )
            }
            if briefing.tasks.isEmpty {
                LabEmptyLine(text: "No action items. Refresh after the next market close.")
            } else {
                ForEach(briefing.tasks.prefix(10)) { item in
                    Button {
                        if item.destination == "options" {
                            mode = .options
                        } else if let symbol = item.symbol {
                            mode = .invest
                            if item.category == "data" {
                                researchSymbol = symbol
                                workflowSymbol = symbol
                                Task { await store.refreshMarket(symbol) }
                            } else {
                                openResearch(symbol)
                            }
                        }
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: item.severity == "critical" ? "exclamationmark.triangle.fill" : item.severity == "opportunity" ? "sparkles" : "circle.fill")
                                .foregroundStyle(item.severity == "critical" ? Color.red : item.severity == "opportunity" ? Color.labGreen : Color.signalOrange)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(labLocalized(item.title)).font(.subheadline.weight(.semibold))
                                Text(labLocalized(item.detail)).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(item.symbol ?? labLocalized(item.category.capitalized))
                                .font(.caption2.weight(.bold))
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 5)
                    }
                    .buttonStyle(.plain)
                }
            }
            Text(labLocalized(briefing.scope)).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private var portfolioPerformance: some View {
        let performance = store.snapshot.portfolioPerformance
        return LabSection(
            title: "Paper portfolio performance",
            subtitle: "Estimated equity and open P&L from cached daily closes.",
            badge: "EOD ESTIMATE"
        ) {
            HStack(spacing: 10) {
                LabMetricCard(
                    label: "Account value",
                    value: performance.estimatedAccountValue.currency,
                    detail: "\(performance.estimatedCash.currency) cash"
                )
                LabMetricCard(
                    label: "Total return",
                    value: "\(performance.totalReturnPercent.decimal >= 0 ? "+" : "")\(performance.totalReturnPercent)%",
                    detail: performance.totalPNL.currency
                )
            }
            HStack(spacing: 10) {
                LabMetricCard(
                    label: "Market value",
                    value: performance.marketValue.currency,
                    detail: "\(performance.openCostBasis.currency) cost"
                )
                LabMetricCard(
                    label: "Unrealized",
                    value: performance.unrealizedPNL.currency,
                    detail: "\(performance.positions.count) open positions"
                )
            }
            if performance.positions.isEmpty {
                LabEmptyLine(text: "Record a paper position to calculate open performance.")
            } else {
                ForEach(performance.positions) { item in
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(item.symbol).font(.headline)
                            Text("\(item.quantity) \(labLocalized(item.assetType.capitalized)) · \(item.referencePrice.currency)")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 3) {
                            Text(item.unrealizedPNL.currency)
                                .font(.subheadline.monospacedDigit().weight(.semibold))
                                .foregroundStyle(item.unrealizedPNL.decimal < 0 ? Color.red : Color.labGreen)
                            Text("\(item.unrealizedPercent.decimal >= 0 ? "+" : "")\(item.unrealizedPercent)% · \(labLocalized(item.decisionLabel ?? "No decision"))")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
            if performance.history.count > 1 {
                Chart(performance.history) { point in
                    LineMark(
                        x: .value("Date", point.tradingDate),
                        y: .value("Equity", NSDecimalNumber(decimal: point.equity.decimal).doubleValue)
                    )
                    .foregroundStyle(Color.labGreen)
                    .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                }
                .chartXAxis(.hidden)
                .frame(height: 170)
                .accessibilityLabel("Paper portfolio equity history")
                if let latest = performance.history.last {
                    Text("\(latest.tradingDate) · \(latest.cash.currency) cash · \(latest.unrealizedPNL.currency) unrealized")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
            Text(labLocalized(performance.disclaimer)).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private var filteredScreenerItems: [ScreenerItem] {
        if screenerSegment == "all" { return store.snapshot.watchlistScreener.items }
        return store.snapshot.watchlistScreener.items.filter { $0.segment == screenerSegment }
    }

    private var watchlistScreener: some View {
        LabSection(
            title: "Watchlist screener",
            subtitle: "Risk actions and current candidates are ranked first.",
            badge: "SORTED"
        ) {
            Picker("Screener view", selection: $screenerSegment) {
                Text("All symbols").tag("all")
                Text("Risk actions").tag("risk")
                Text("Candidates").tag("opportunity")
                Text("Positions").tag("position")
                Text("Watch / avoid").tag("watch")
                Text("Data issues").tag("data")
            }
            .pickerStyle(.menu)
            if filteredScreenerItems.isEmpty {
                LabEmptyLine(text: "No symbols match this view.")
            } else {
                ForEach(filteredScreenerItems) { item in
                    Button {
                        openResearch(item.symbol)
                    } label: {
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(item.symbol).font(.headline)
                                Text("\(labLocalized(item.signalLabel)) · \(labLocalized(item.stateLabel))")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            VStack(alignment: .trailing, spacing: 3) {
                                Text(item.score.map { "\($0) / 100" } ?? "—")
                                    .font(.subheadline.monospacedDigit().weight(.semibold))
                                Text(item.latestClose?.currency ?? labLocalized(item.freshness.capitalized))
                                    .font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 5)
                    }
                    .buttonStyle(.plain)
                }
            }
            Text(labLocalized(store.snapshot.watchlistScreener.sort)).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private var watchlist: some View {
        LabSection(title: "Watchlist research", subtitle: "Open cached research or refresh one followed ticker directly.", badge: "END OF DAY") {
            Button("Refresh all tickers") { Task { await store.refreshWatchlistDecisions() } }
                .buttonStyle(LabButtonStyle())
                .disabled(store.isLoading || store.snapshot.watchlist.isEmpty)
            if store.snapshot.watchlistResearch.isEmpty {
                LabEmptyLine(text: "Add the first company you want to follow.")
            } else {
                ForEach(store.snapshot.watchlistResearch) { research in
                    HStack(spacing: 8) {
                        Button {
                            openResearch(research.symbol)
                        } label: {
                            HStack(spacing: 12) {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(research.symbol).font(.headline)
                                    Text(labLocalized(research.stateLabel ?? "No cached evidence"))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                if let close = research.latestClose, let change = research.changePercent {
                                    VStack(alignment: .trailing, spacing: 4) {
                                        Text(close.currency).font(.subheadline.monospacedDigit().weight(.semibold))
                                        Text("\(change.decimal >= 0 ? "+" : "")\(change)%")
                                            .font(.caption.monospacedDigit())
                                            .foregroundStyle(change.decimal < 0 ? Color.red : Color.labGreen)
                                    }
                                } else {
                                    Text("Needs refresh")
                                        .font(.system(size: 11, weight: .bold))
                                        .foregroundStyle(Color.signalOrange)
                                }
                            }
                            .frame(maxWidth: .infinity)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Open \(research.symbol) research")
                        Button {
                            researchSymbol = research.symbol
                            workflowSymbol = research.symbol
                            Task { await store.refreshMarket(research.symbol) }
                        } label: {
                            Image(systemName: "arrow.clockwise")
                                .frame(width: 38, height: 38)
                        }
                        .buttonStyle(.bordered)
                        .disabled(store.isLoading)
                        .accessibilityLabel("Refresh \(research.symbol)")
                    }
                    .padding(10)
                    .background(Color.white, in: RoundedRectangle(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.labLine))
                }
            }
        }
    }

    private var marketEvidence: some View {
        LabSection(
            title: "Daily market evidence",
            subtitle: "End-of-day bars and a transparent 20/50-day structure check.",
            badge: "RESEARCH ONLY"
        ) {
            HStack(spacing: 10) {
                TextField("AAPL", text: $researchSymbol)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .padding(.horizontal, 12)
                    .frame(minHeight: 44)
                    .background(Color.white, in: RoundedRectangle(cornerRadius: 11))
                    .overlay(RoundedRectangle(cornerRadius: 11).stroke(Color.labLine))
                Button("Refresh") {
                    let symbol = researchSymbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
                    workflowSymbol = symbol
                    Task { await store.refreshMarket(symbol) }
                }
                    .buttonStyle(LabButtonStyle(compact: true))
                    .disabled(researchSymbol.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if let research = store.marketResearch, research.available {
                let bars = research.bars ?? []
                if bars.count > 1 {
                    Chart(bars) { bar in
                        LineMark(
                            x: .value("Trading day", bar.tradingDate),
                            y: .value("Close", bar.closeValue)
                        )
                        .foregroundStyle(Color.labGreen)
                        .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                        if bar.id == bars.last?.id {
                            PointMark(
                                x: .value("Trading day", bar.tradingDate),
                                y: .value("Close", bar.closeValue)
                            )
                            .foregroundStyle(Color.signalOrange)
                            .symbolSize(54)
                        }
                    }
                    .chartXAxis(.hidden)
                    .chartYScale(domain: priceDomain(bars))
                    .chartYAxis {
                        AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) {
                            AxisGridLine().foregroundStyle(Color.labLine)
                            AxisValueLabel()
                        }
                    }
                    .frame(height: 190)
                    .accessibilityLabel("\(research.symbol) historical daily closing-price chart")
                    HStack {
                        Text(bars.first?.tradingDate ?? "")
                        Spacer()
                        Text(bars.last?.tradingDate ?? "")
                    }
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
                    Chart(bars) { bar in
                        BarMark(
                            x: .value("Trading day", bar.tradingDate),
                            y: .value("Volume", bar.volumeValue)
                        )
                        .foregroundStyle(Color.signalOrange.opacity(0.35))
                    }
                    .chartXAxis(.hidden)
                    .chartYAxis(.hidden)
                    .frame(height: 54)
                    .accessibilityLabel("\(research.symbol) historical daily volume chart")
                }
                HStack(spacing: 10) {
                    LabMetricCard(
                        label: "Structure",
                        value: research.stateLabel ?? "—",
                        detail: "\(research.symbol) · \(research.tradingDate ?? "")"
                    )
                    LabMetricCard(
                        label: "Latest close",
                        value: (research.latestClose ?? "0").currency,
                        detail: "\(research.changePercent ?? "0")% vs prior"
                    )
                }
                if let quote = research.realtimeQuote {
                    LabMetricCard(
                        label: "Latest IEX trade",
                        value: quote.available ? (quote.latestPrice ?? "0").currency : "—",
                        detail: quote.available
                            ? "\(labLocalized(quote.sessionPhase ?? "unknown")) · IEX · \(labLocalized("Latest observed trade"))"
                            : labLocalized(quote.reason ?? "Configure Alpaca for an observed live price")
                    )
                    if let scope = quote.scope {
                        Text(labLocalized(scope)).font(.caption2).foregroundStyle(.secondary)
                    }
                }
                if let range = research.rangeStats {
                    HStack(spacing: 10) {
                    LabMetricCard(
                        label: "Observed range",
                        value: "\(range.lowClose.currency) – \(range.highClose.currency)",
                        detail: labLocalized(range.periodLabel)
                        )
                        LabMetricCard(
                            label: "Period return",
                            value: "\(range.periodReturnPercent.decimal >= 0 ? "+" : "")\(range.periodReturnPercent)%",
                            detail: "First to latest close"
                        )
                    }
                    HStack(spacing: 10) {
                        LabMetricCard(
                            label: "Maximum drawdown",
                            value: "\(range.maxDrawdownPercent)%",
                            detail: "Close-based path"
                        )
                        LabMetricCard(
                            label: "Historical volatility",
                            value: "\(range.annualizedVolatilityPercent)%",
                            detail: "Annualized daily"
                        )
                    }
                    Text(labLocalized("Latest volume \(range.latestVolume.formatted()) · \(range.latestVolumeVsAveragePercent)% of the \(range.averageVolume.formatted()) average. Descriptive history only."))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let quality = research.dataQuality {
                    HStack(spacing: 10) {
                        LabMetricCard(
                            label: "Data quality",
                            value: "\(quality.score) / 100",
                            detail: labLocalized(quality.status)
                        )
                        LabMetricCard(
                            label: "Decision gate",
                            value: quality.decisionEligible ? "Eligible" : "Blocked",
                            detail: "\(quality.latestAgeDays ?? 0) days old · \(quality.priceAdjustment)"
                        )
                    }
                    Text((quality.blockers + quality.warnings).map(labLocalized).joined(separator: " "))
                        .font(.caption2)
                        .foregroundStyle(quality.blockers.isEmpty ? Color.secondary : Color.red)
                }
                Text(labLocalized(research.explanation ?? ""))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let scenario = research.historicalScenario {
                    Text(labLocalized("Historical scenario: \(scenario.strategyReturnPercent)% vs buy-and-hold \(scenario.buyHoldReturnPercent)%; max drawdown \(scenario.maxDrawdownPercent)%."))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } else {
                LabEmptyLine(
                    text: store.marketResearch?.reason
                        ?? "Save an Alpha Vantage key in Settings on this Mac, then refresh a symbol."
                )
            }
        }
        .onAppear {
            if researchSymbol.isEmpty, let first = store.snapshot.watchlist.first?.symbol {
                researchSymbol = first
            }
        }
        .onChange(of: store.snapshot.watchlist.first?.symbol) { _, symbol in
            if researchSymbol.isEmpty, let symbol { researchSymbol = symbol }
        }
    }

    private var fundamentalEvidence: some View {
        LabSection(
            title: "SEC fundamentals",
            subtitle: "Company-reported annual XBRL facts and recent filings from SEC EDGAR.",
            badge: "FREE PUBLIC DATA"
        ) {
            HStack {
                Text("No SEC account or API key required · cached for 24 hours")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Refresh SEC filings") {
                    Task { await store.refreshFundamentals(researchSymbol) }
                }
                .buttonStyle(.bordered)
                .disabled(researchSymbol.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if let data = store.fundamentalResearch,
               data.available,
               let metrics = data.metrics {
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(data.symbol) · \(data.companyName ?? data.symbol)")
                        .font(.headline)
                    Text("\(labLocalized("Fiscal year")) \(data.fiscalYear.map(String.init) ?? "—") · \(data.periodEnd ?? "—") · CIK \(data.cik ?? "—")")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                if let profile = data.companyProfile {
                    HStack(spacing: 10) {
                        LabMetricCard(label: "Industry", value: profile.industry ?? "—", detail: profile.sic.map { "SIC \($0)" } ?? "SEC company profile")
                        LabMetricCard(label: "Exchange", value: profile.exchange ?? "—", detail: profile.location ?? "—")
                    }
                }
                if let changes = data.changes, changes.detected {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Changed since last SEC refresh").font(.subheadline.weight(.semibold))
                        Text(labLocalized(changes.summary)).font(.caption).foregroundStyle(.secondary)
                        if !changes.newFilings.isEmpty {
                            Text(changes.newFilings.map { "\($0.form) · \($0.filed)" }.joined(separator: " · "))
                                .font(.caption2.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.signalOrange.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
                }
                HStack(spacing: 10) {
                    LabMetricCard(
                        label: "Revenue",
                        value: compactUSD(metrics.revenue),
                        detail: "Revenue growth \(signedPercent(metrics.revenueGrowthPercent))"
                    )
                    LabMetricCard(
                        label: "Net income",
                        value: compactUSD(metrics.netIncome),
                        detail: "Net margin \(signedPercent(metrics.netMarginPercent))"
                    )
                }
                if let valuation = data.valuation {
                    Text("VALUATION SNAPSHOT").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                    HStack(spacing: 10) {
                        LabMetricCard(label: "P / E", value: valuation.pe ?? "—", detail: valuation.basis ?? valuation.reason ?? "—")
                        LabMetricCard(label: "P / sales", value: valuation.priceToSales ?? "—", detail: valuation.priceDate ?? "—")
                    }
                    HStack(spacing: 10) {
                        LabMetricCard(label: "P / free cash flow", value: valuation.priceToFCF ?? "—", detail: valuation.price.map { $0.currency } ?? "—")
                        LabMetricCard(label: "Dividend yield", value: valuation.dividendYieldPercent.map { "\($0)%" } ?? "—", detail: valuation.historicalPERange.map { "P/E \($0.low)–\($0.high) · \($0.observations)" } ?? "Historical range unavailable")
                    }
                }
                if let quarterlyHistory = data.quarterlyHistory, !quarterlyHistory.isEmpty {
                    Text("QUARTERLY TRENDS").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                    if let trends = data.quarterlyTrends {
                        HStack(spacing: 10) {
                            LabMetricCard(label: "QoQ revenue", value: signedPercent(trends.revenueQoQPercent), detail: trends.latestPeriodEnd ?? "—")
                            LabMetricCard(label: "YoY revenue", value: signedPercent(trends.revenueYoYPercent), detail: trends.latestPeriodEnd ?? "—")
                        }
                    }
                    ForEach(Array(quarterlyHistory.reversed())) { period in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(period.fiscalPeriod ?? "Q") \(period.fiscalYear)").font(.subheadline.weight(.semibold))
                                Text(period.periodEnd).font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(compactUSD(period.revenue)).font(.caption.monospacedDigit())
                            Text(compactUSD(period.netIncome)).font(.caption.monospacedDigit())
                        }.padding(.vertical, 4)
                    }
                }
                HStack(spacing: 10) {
                    LabMetricCard(
                        label: "Free cash flow",
                        value: compactUSD(metrics.freeCashFlow),
                        detail: "Operating cash flow \(compactUSD(metrics.operatingCashFlow))"
                    )
                    LabMetricCard(
                        label: "Assets",
                        value: compactUSD(metrics.assets),
                        detail: "Liabilities \(compactUSD(metrics.liabilities))"
                    )
                }
                HStack(spacing: 10) {
                    LabMetricCard(
                        label: "Liabilities / assets",
                        value: signedPercent(metrics.liabilitiesToAssetsPercent, showPlus: false),
                        detail: "Shareholders' equity \(compactUSD(metrics.equity))"
                    )
                    LabMetricCard(
                        label: "Diluted EPS",
                        value: metrics.dilutedEPS.map {
                            $0.formatted(.currency(code: "USD").precision(.fractionLength(2)))
                        } ?? "—",
                        detail: "\(data.annualHistory.count) annual periods"
                    )
                }
                HStack(spacing: 10) {
                    LabMetricCard(
                        label: "Annual dividends / share",
                        value: metrics.dividendsPerShare.map {
                            $0.formatted(.currency(code: "USD").precision(.fractionLength(2)))
                        } ?? "—",
                        detail: "SEC annual fact"
                    )
                    LabMetricCard(
                        label: "Net income growth",
                        value: signedPercent(metrics.netIncomeGrowthPercent),
                        detail: "Latest two annual periods"
                    )
                }
                if !data.annualHistory.isEmpty {
                    Text("ANNUAL HISTORY").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                    ForEach(Array(data.annualHistory.reversed())) { period in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("FY \(period.fiscalYear)").font(.subheadline.weight(.semibold))
                                Text(period.periodEnd).font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                            VStack(alignment: .trailing, spacing: 2) {
                                Text(compactUSD(period.revenue)).font(.caption.monospacedDigit())
                                Text("Revenue").font(.caption2).foregroundStyle(.secondary)
                            }
                            VStack(alignment: .trailing, spacing: 2) {
                                Text(compactUSD(period.netIncome)).font(.caption.monospacedDigit())
                                Text("Net income").font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
                if !data.filings.isEmpty {
                    Text("RECENT SEC FILINGS").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                    ForEach(data.filings) { filing in
                        if let url = URL(string: filing.url) {
                            Link(destination: url) {
                                HStack {
                                    Text(filing.form).font(.subheadline.weight(.semibold))
                                    Spacer()
                                    Text("\(labLocalized("Filed")) \(filing.filed)").font(.caption).foregroundStyle(.secondary)
                                    Image(systemName: "arrow.up.right.square")
                                }
                            }
                        }
                    }
                }
                if let comparison = data.filingComparison {
                    Text("SEC FILING COMPARISON").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                    if comparison.available {
                        if let risk = comparison.sections?.riskFactors {
                            filingComparisonRow(title: "Risk factors", change: risk)
                        }
                        if let management = comparison.sections?.managementDiscussion {
                            filingComparisonRow(title: "Management discussion", change: management)
                        }
                    } else {
                        LabEmptyLine(text: comparison.reason ?? "Two comparable 10-K filings are required.")
                    }
                }
                Text(labLocalized(data.scope ?? ""))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                LabEmptyLine(
                    text: store.fundamentalResearch?.reason
                        ?? "Refresh SEC fundamentals for the symbol above."
                )
            }
        }
    }

    private func filingComparisonRow(title: String, change: FilingSectionChange) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(LocalizedStringKey(title)).font(.subheadline.weight(.semibold))
                Spacer()
                Text("\(change.similarityPercent)%").font(.caption.monospacedDigit())
            }
            if let added = change.added.first { Text("+ \(added)").font(.caption2).foregroundStyle(Color.labGreen) }
            if let removed = change.removed.first { Text("− \(removed)").font(.caption2).foregroundStyle(.red) }
        }
        .padding(10)
        .background(Color.labPaper, in: RoundedRectangle(cornerRadius: 10))
    }

    private var earningsCalendar: some View {
        let calendar = store.snapshot.earningsCalendar
        return LabSection(
            title: "Earnings calendar",
            subtitle: "Upcoming estimated report dates for companies on your watchlist.",
            badge: "WATCHLIST"
        ) {
            Button("Refresh earnings calendar") { Task { await store.refreshEarningsCalendar() } }
                .buttonStyle(.bordered)
            if calendar.events.isEmpty {
                LabEmptyLine(text: calendar.reason ?? "No upcoming watchlist earnings in the current three-month calendar.")
            } else {
                ForEach(calendar.events) { event in
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("\(event.symbol) · \(event.name)").font(.subheadline.weight(.semibold))
                            Text(event.fiscalDateEnding ?? "—").font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 3) {
                            Text(event.reportDate).font(.caption.monospacedDigit())
                            Text("\(event.daysUntil) days").font(.caption2).foregroundStyle(.secondary)
                        }
                    }.padding(.vertical, 4)
                }
            }
            Text(labLocalized(calendar.scope ?? "")).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private var secEventMonitor: some View {
        let center = store.snapshot.secEvents
        return LabSection(
            title: "SEC filing monitor",
            subtitle: "Watchlist filing timeline from the same cached EDGAR evidence used by Strategy Lab.",
            badge: "CACHED WATCHLIST"
        ) {
            HStack(spacing: 10) {
                LabMetricCard(label: "Recent 7 days", value: "\(center.recentCount)", detail: "Official submissions")
                LabMetricCard(label: "Material updates", value: "\(center.attentionCount)", detail: "8-K attention events")
            }
            HStack(spacing: 10) {
                LabMetricCard(label: "Annual reports", value: "\(center.annualCount)", detail: "10-K and amendments")
                LabMetricCard(label: "Quarterly reports", value: "\(center.quarterlyCount)", detail: "10-Q and amendments")
            }
            if center.events.isEmpty {
                LabEmptyLine(text: "No cached SEC filings for watchlist companies.")
            } else {
                ForEach(center.events.prefix(12)) { event in
                    if let url = URL(string: event.url) {
                        Link(destination: url) {
                            HStack(spacing: 10) {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text("\(event.symbol) · \(event.form)").font(.subheadline.weight(.semibold))
                                    Text(labLocalized(event.title)).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Text(event.filed).font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
                                Image(systemName: "arrow.up.right.square")
                            }
                        }
                    }
                }
            }
            Text(labLocalized(center.scope)).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func compactUSD(_ value: Double?) -> String {
        guard let value else { return "—" }
        let magnitude = abs(value)
        let (divisor, suffix): (Double, String) = magnitude >= 1_000_000_000_000
            ? (1_000_000_000_000, "T")
            : magnitude >= 1_000_000_000
                ? (1_000_000_000, "B")
                : magnitude >= 1_000_000
                    ? (1_000_000, "M")
                    : magnitude >= 1_000 ? (1_000, "K") : (1, "")
        let decimals = divisor == 1 ? 2 : 1
        return "\(value < 0 ? "-" : "")$\(String(format: "%.*f", decimals, magnitude / divisor))\(suffix)"
    }

    private func signedPercent(_ value: String?, showPlus: Bool = true) -> String {
        guard let value else { return "—" }
        return "\(showPlus && !value.hasPrefix("-") ? "+" : "")\(value)%"
    }

    private func priceDomain(_ bars: [MarketBar]) -> ClosedRange<Double> {
        let closes = bars.map(\.closeValue)
        let lower = closes.min() ?? 0
        let upper = closes.max() ?? 1
        let padding = max((upper - lower) * 0.08, upper * 0.01, 0.01)
        return (lower - padding)...(upper + padding)
    }

    private var decisionCenter: some View {
        LabSection(
            title: "Strategy Lab 4.1",
            subtitle: "Data-gated decisions, outcome validation, and parameter sensitivity.",
            badge: "EOD SCORE"
        ) {
            if store.snapshot.decisionCenter.latest.isEmpty {
                LabEmptyLine(text: "Refresh daily bars and generate the first decision.")
            } else {
                ForEach(store.snapshot.decisionCenter.latest) { decision in
                    Button {
                        openResearch(decision.symbol)
                    } label: {
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(decision.symbol).font(.headline)
                                Text(labLocalized(decision.signalLabel))
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(Color.signalOrange)
                            }
                            Spacer()
                            VStack(alignment: .trailing, spacing: 4) {
                                Text(decision.score.map { "\($0) / 100" } ?? labLocalized("DATA GATE"))
                                    .font(.subheadline.monospacedDigit().weight(.semibold))
                                Text(labLocalized(decision.tradingDate ?? "No current bar"))
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(12)
                        .background(Color.white, in: RoundedRectangle(cornerRadius: 14))
                        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.labLine))
                    }
                    .buttonStyle(.plain)
                }
            }
            Button("Generate for \(researchSymbol.isEmpty ? "symbol" : researchSymbol.uppercased())") {
                let symbol = researchSymbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
                workflowSymbol = symbol
                Task { await store.generateDecision(symbol) }
            }
            .buttonStyle(LabButtonStyle())
            .disabled(researchSymbol.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.isLoading)
        }
    }

    @ViewBuilder private var decisionDetail: some View {
        if let bundle = store.decisionBundle, let decision = bundle.latest {
            LabSection(
                title: "\(decision.symbol) · \(labLocalized(decision.signalLabel))",
                subtitle: labLocalized(decision.change.summary),
                badge: decision.modelVersion.uppercased()
            ) {
                HStack(spacing: 10) {
                    LabMetricCard(
                        label: "Decision score",
                        value: decision.score.map(String.init) ?? "—",
                        detail: decision.quality.replacingOccurrences(of: "_", with: " ")
                    )
                    LabMetricCard(
                        label: "Risk budget",
                        value: decision.riskPlan.riskBudget.currency,
                        detail: "\(decision.position.accountPercent)% of account held"
                    )
                }
                if let quality = decision.dataQuality {
                    HStack(spacing: 10) {
                        LabMetricCard(label: "Data quality", value: "\(quality.score) / 100", detail: labLocalized(quality.status))
                        LabMetricCard(label: "Scoring readiness", value: quality.decisionEligible ? "Ready to score" : "Needs more data", detail: "\(quality.observations ?? 0) bars")
                    }
                }
                HStack(spacing: 10) {
                    LabMetricCard(
                        label: "Position capacity",
                        value: decision.riskPlan.remainingPositionCapacity.currency,
                        detail: "Before a new entry"
                    )
                    LabMetricCard(
                        label: "Observed range",
                        value: decision.observedRange.low.map { "\($0.currency) – \((decision.observedRange.high ?? "0").currency)" } ?? "—",
                        detail: "Not a price target"
                    )
                }
                if let strategy = decision.strategy {
                    Text("\(labLocalized(strategy.label)) · \(strategy.technicalWeight)% \(labLocalized("technical")) · \(strategy.fundamentalWeight)% \(labLocalized("fundamentals")) · \(strategy.valuationWeight ?? 0)% \(labLocalized("valuation")) · \(strategy.portfolioWeight)% \(labLocalized("position fit"))\(strategy.fundamentalsPeriodEnd.map { " · SEC \($0)" } ?? ""). \(labLocalized(strategy.horizonNote))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let pricePlan = decision.pricePlan, pricePlan.available {
                    let buyZone = pricePlan.buyZoneLow.flatMap { low in
                        pricePlan.buyZoneHigh.map { high in "\(low.currency) – \(high.currency)" }
                    } ?? "—"
                    HStack(spacing: 10) {
                        LabMetricCard(
                            label: "Buy / add zone",
                            value: buyZone,
                            detail: "Pullback scenario"
                        )
                        LabMetricCard(
                            label: "Breakout buy trigger",
                            value: pricePlan.breakoutTrigger?.currency ?? "—",
                            detail: "Prior high + 0.1 ATR"
                        )
                    }
                    HStack(spacing: 10) {
                        LabMetricCard(
                            label: "Risk stop reference",
                            value: pricePlan.riskStop?.currency ?? "—",
                            detail: "SMA / ATR scenario"
                        )
                        LabMetricCard(
                            label: "Sell target 1",
                            value: pricePlan.target1?.currency ?? "—",
                            detail: "\(labLocalized("Minimum saved R")) · \(pricePlan.minimumRewardRisk ?? "—"):1"
                        )
                    }
                    HStack(spacing: 10) {
                        LabMetricCard(
                            label: "Sell target 2",
                            value: pricePlan.target2?.currency ?? "—",
                            detail: "One additional R"
                        )
                        LabMetricCard(
                            label: "ATR (14)",
                            value: pricePlan.atr14?.currency ?? "—",
                            detail: "Recent daily range"
                        )
                    }
                    VStack(alignment: .leading, spacing: 7) {
                        Text(labLocalized("How prices are calculated"))
                            .font(.caption.weight(.bold))
                        if let action = pricePlan.action {
                            Text(labLocalized(action)).font(.caption)
                        }
                        ForEach(pricePlan.formula ?? [], id: \.self) {
                            Text("• \(labLocalized($0))").font(.caption)
                        }
                        if let disclaimer = pricePlan.disclaimer {
                            Text(labLocalized(disclaimer)).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.labGreen.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
                } else if let reason = decision.pricePlan?.reason {
                    LabEmptyLine(text: labLocalized(reason))
                } else {
                    LabEmptyLine(text: labLocalized("Generate a new decision to calculate price levels with Decision v4.0."))
                }
                if let strategy = decision.strategy, let origin = strategy.origin {
                    VStack(alignment: .leading, spacing: 7) {
                        Text(labLocalized("Strategy logic and source"))
                            .font(.caption.weight(.bold))
                        Text(labLocalized(origin)).font(.caption)
                        ForEach(strategy.decisionRules ?? [], id: \.self) {
                            Text("• \(labLocalized($0))").font(.caption)
                        }
                        if let sources = strategy.dataSources, !sources.isEmpty {
                            Text("\(labLocalized("Data sources"))：\(sources.map(labLocalized).joined(separator: " · "))")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        if let method = strategy.pricePlanMethod {
                            Text(labLocalized(method)).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.labPaper, in: RoundedRectangle(cornerRadius: 12))
                }
                ForEach(decision.factors) { factor in
                    VStack(alignment: .leading, spacing: 7) {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(labLocalized(factor.label)).font(.subheadline.weight(.semibold))
                                Text(labLocalized(factor.value)).font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text("\(factor.score) / \(factor.maxScore)")
                                .font(.caption.monospacedDigit().weight(.semibold))
                        }
                        ProgressView(value: Double(factor.score), total: Double(factor.maxScore))
                            .tint(Color.signalOrange)
                    }
                    .padding(.vertical, 4)
                }
                Group {
                    Text("SUPPORTING EVIDENCE").font(.caption2.weight(.bold)).foregroundStyle(Color.labGreen)
                    ForEach(decision.evidence, id: \.self) { Text("• \(labLocalized($0))").font(.caption) }
                    Text("COUNTER-EVIDENCE").font(.caption2.weight(.bold)).foregroundStyle(Color.signalOrange)
                    if decision.counterEvidence.isEmpty {
                        Text("• No additional item recorded.").font(.caption)
                    } else {
                        ForEach(decision.counterEvidence, id: \.self) { Text("• \(labLocalized($0))").font(.caption) }
                    }
                }
                Text("\(labLocalized("Invalidation"))：\(labLocalized(decision.invalidation))")
                    .font(.caption.weight(.semibold))
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.signalOrange.opacity(0.10), in: RoundedRectangle(cornerRadius: 12))
                if bundle.backtest.available {
                    if let holdout = bundle.backtest.outOfSample, holdout.available {
                        LabMetricCard(
                            label: "Frozen holdout",
                            value: "\(holdout.strategyReturnPercent ?? "—")%",
                            detail: "\(holdout.sessions ?? 0) \(labLocalized("sessions")) · \(labLocalized("parameters frozen"))"
                        )
                    } else if let remaining = bundle.backtest.outOfSample?.remainingSessions {
                        LabEmptyLine(text: "\(remaining) \(labLocalized("more evaluated sessions before a 60-session holdout"))")
                    } else if let reason = bundle.backtest.outOfSample?.reason {
                        LabEmptyLine(text: labLocalized(reason))
                    }
                    HStack(spacing: 10) {
                        LabMetricCard(
                            label: "Full-sample return",
                            value: "\(bundle.backtest.strategyReturnPercent ?? "—")%",
                            detail: "10 bps each side"
                        )
                        LabMetricCard(
                            label: "SPY benchmark",
                            value: bundle.backtest.benchmarkAvailable == true ? "\(bundle.backtest.spyReturnPercent ?? "—")%" : "Not cached",
                            detail: bundle.backtest.benchmarkAvailable == true ? "Relative \(bundle.backtest.relativeToSPYPercent ?? "—")%" : "Refresh SPY daily bars"
                        )
                    }
                    HStack(spacing: 10) {
                        LabMetricCard(
                            label: "Execution timing",
                            value: "Next session close",
                            detail: "Signal at prior close; costs apply on entry and exit."
                        )
                        LabMetricCard(
                            label: "Trade sample",
                            value: "\(bundle.backtest.completedTrades ?? 0) \(labLocalized("completed trades"))",
                            detail: bundle.backtest.winRatePercent.map { "\($0)% \(labLocalized("Win rate"))" }
                                ?? "Win rate hidden until 10 completed trades."
                        )
                    }
                    if let curve = bundle.backtest.equityCurve, curve.count > 1 {
                        Chart(curve) { point in
                            LineMark(
                                x: .value("Date", point.tradingDate),
                                y: .value("Equity", point.equityValue)
                            )
                            .foregroundStyle(Color.labGreen)
                        }
                        .chartXAxis(.hidden)
                        .frame(height: 170)
                    }
                    ForEach((bundle.backtest.trades ?? []).prefix(12)) { trade in
                        HStack {
                            VStack(alignment: .leading, spacing: 3) {
                                Text("\(trade.entryDate) → \(trade.exitDate)").font(.caption.weight(.semibold))
                                Text("\(trade.entryPrice.currency) → \(trade.exitPrice.currency)")
                                    .font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text("\(trade.returnPercent.decimal >= 0 ? "+" : "")\(trade.returnPercent)%")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(trade.returnPercent.decimal < 0 ? Color.red : Color.labGreen)
                        }
                    }
                    Text("\(labLocalized(bundle.backtest.rules ?? "")) \(labLocalized(bundle.backtest.assumption ?? ""))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    LabEmptyLine(text: bundle.backtest.reason ?? "More bars are required for backtesting.")
                }
                if let sensitivity = bundle.backtest.parameterSensitivity, !sensitivity.isEmpty {
                    Text("PARAMETER SENSITIVITY").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                    ForEach(sensitivity) { item in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(labLocalized(item.label.capitalized)).font(.caption.weight(.semibold))
                                Text("\(item.entryThreshold) / \(item.exitThreshold) entry / exit gates")
                                    .font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text("\(item.strategyReturnPercent ?? "—")%")
                                .font(.caption.monospacedDigit())
                        }
                    }
                    if let stability = bundle.backtest.stability {
                        Text("\(labLocalized("Parameter stability")): \(labLocalized(stability.label)) · \(stability.returnRangePoints) point range")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
                if let validation = bundle.validation {
                    HStack(spacing: 10) {
                        LabMetricCard(
                            label: "Stored outcomes",
                            value: "\(validation.eligibleDecisions)",
                            detail: validation.targetFirstRatePercent.map { "\($0)% target first" } ?? "Building history"
                        )
                        LabMetricCard(
                            label: "Outcome excursion",
                            value: "\(validation.averageMaximumAdverseExcursionPercent ?? "—")% MAE",
                            detail: "\(validation.averageMaximumFavorableExcursionPercent ?? "—")% MFE"
                        )
                    }
                    ForEach(validation.outcomes.prefix(8)) { outcome in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(outcome.decisionDate).font(.caption.weight(.semibold))
                                Text(labLocalized(outcome.resolution)).font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text("MAE \(outcome.maximumAdverseExcursionPercent)% · MFE \(outcome.maximumFavorableExcursionPercent)%")
                                .font(.caption2.monospacedDigit())
                        }
                    }
                    Text(labLocalized(validation.scope)).font(.caption2).foregroundStyle(.secondary)
                }
                if let changes = decision.change.factorChanges, !changes.isEmpty {
                    Text("WHY THIS CHANGED").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                    ForEach(changes) { change in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(labLocalized(change.label)).font(.caption.weight(.semibold))
                                Text(labLocalized(change.direction)).font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text("\(change.previousScore) → \(change.currentScore) (\(change.scoreDelta > 0 ? "+" : "")\(change.scoreDelta))")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(change.scoreDelta < 0 ? Color.red : Color.labGreen)
                        }
                    }
                }
                if bundle.history.count > 1 {
                    Text("DECISION HISTORY").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                    ForEach(bundle.history.prefix(8)) { item in
                        HStack {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(labLocalized(item.signalLabel)).font(.subheadline.weight(.semibold))
                                Text(labLocalized(item.change.summary)).font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(item.score.map(String.init) ?? "—")
                                .font(.caption.monospacedDigit())
                        }
                        .padding(.vertical, 5)
                    }
                }
                Text(labLocalized(decision.disclaimer)).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var positions: some View {
        LabSection(title: "Paper positions", subtitle: "Cost basis from the shared ledger.", badge: "APPEND ONLY") {
            if store.snapshot.portfolio.positions.isEmpty {
                LabEmptyLine(text: "Paper positions will appear after your first trade.")
            } else {
                ForEach(store.snapshot.portfolio.positions) { position in
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 7) {
                                Text(position.symbol).font(.headline)
                                Text(labLocalized(position.assetType.capitalized))
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundStyle(.secondary)
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 3)
                                    .background(Color.labPaper, in: Capsule())
                            }
                            Text("\(position.quantity) @ \(position.averageCost.currency)")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(position.realizedPNL.currency)
                            .font(.subheadline.monospacedDigit().weight(.semibold))
                            .foregroundStyle(position.realizedPNL.decimal < 0 ? Color.red : Color.labGreen)
                    }
                    .padding(.vertical, 8)
                    if position.id != store.snapshot.portfolio.positions.last?.id { Divider() }
                }
            }
        }
    }

    private var portfolioRisk: some View {
        LabSection(
            title: "Portfolio exposure",
            subtitle: "Cached closes when available; cost basis otherwise.",
            badge: "DESCRIPTIVE"
        ) {
            HStack(spacing: 10) {
                LabMetricCard(
                    label: "Gross exposure",
                    value: store.snapshot.portfolioRisk.grossExposure.currency,
                    detail: "\(store.snapshot.portfolioRisk.positionCount) positions"
                )
                LabMetricCard(
                    label: "Largest weight",
                    value: "\(store.snapshot.portfolioRisk.largestWeightPercent)%",
                    detail: store.snapshot.portfolioRisk.concentrationLabel
                )
            }
            if store.snapshot.portfolioRisk.positions.isEmpty {
                LabEmptyLine(text: "Exposure appears after a paper position is recorded.")
            } else {
                ForEach(store.snapshot.portfolioRisk.positions) { position in
                    VStack(alignment: .leading, spacing: 7) {
                        HStack {
                            Text(position.symbol).font(.subheadline.weight(.semibold))
                            Text(position.sector).font(.caption2).foregroundStyle(.secondary)
                            Text(labLocalized(position.referenceSource == "cached_daily_close" ? "CACHED CLOSE" : "COST BASIS"))
                                .font(.system(size: 11, weight: .bold))
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text("\(position.weightPercent)% · \(position.exposure.currency)")
                                .font(.caption.monospacedDigit())
                        }
                        ProgressView(value: Double(position.weightPercent) ?? 0, total: 100)
                            .tint(Color.signalOrange)
                    }
                    .padding(.vertical, 6)
                }
            }
            if !store.snapshot.portfolioRisk.sectors.isEmpty {
                Divider()
                Text("SECTOR EXPOSURE").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                ForEach(store.snapshot.portfolioRisk.sectors) { sector in
                    HStack {
                        Text(sector.sector).font(.caption)
                        Spacer()
                        Text("\(sector.weightPercent)% · \(sector.exposure.currency)")
                            .font(.caption.monospacedDigit())
                    }
                }
            }
            if !store.snapshot.portfolioRisk.stressScenarios.isEmpty {
                Divider()
                Text("STRESS TESTS").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                ForEach(store.snapshot.portfolioRisk.stressScenarios) { scenario in
                    HStack {
                        VStack(alignment: .leading) {
                            Text(scenario.label).font(.subheadline.weight(.semibold))
                            Text("\(scenario.accountImpactPercent)% of paper account")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(scenario.estimatedImpact.currency)
                            .font(.caption.monospacedDigit()).foregroundStyle(Color.red)
                    }
                }
            }
            if !store.snapshot.portfolioRisk.correlations.isEmpty {
                Divider()
                Text("HIGHEST CORRELATIONS").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                ForEach(store.snapshot.portfolioRisk.correlations) { item in
                    HStack {
                        Text("\(item.left) / \(item.right)").font(.subheadline.weight(.semibold))
                        Spacer()
                        Text("\(item.correlation) · \(item.observations) days")
                            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                }
            }
            Text(labLocalized(store.snapshot.portfolioRisk.disclaimer))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var portfolioActions: some View {
        LabSection(
            title: "Portfolio action list",
            subtitle: "Candidates, limit breaches, reduce reviews, and data gaps.",
            badge: "PAPER ONLY"
        ) {
            if store.snapshot.portfolioActions.actions.isEmpty {
                LabEmptyLine(text: "No portfolio action is pending.")
            } else {
                ForEach(store.snapshot.portfolioActions.actions) { item in
                    HStack(alignment: .top, spacing: 10) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("\(item.symbol) · \(labLocalized(item.label))").font(.subheadline.weight(.semibold))
                            Text(labLocalized(item.reason)).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(item.score.map { "\($0)" } ?? "—").font(.caption.monospacedDigit())
                    }
                }
            }
            Text(labLocalized(store.snapshot.portfolioActions.scope)).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private var rebalanceCalculator: some View {
        LabSection(
            title: "Rebalance calculator",
            subtitle: "SYMBOL:percent targets using cached reference prices.",
            badge: "NO ORDERS"
        ) {
            TextField("AAPL:40, MSFT:30, SPY:20", text: $rebalanceTargets)
                .textInputAutocapitalization(.characters)
                .autocorrectionDisabled()
                .padding(12)
                .background(Color.white, in: RoundedRectangle(cornerRadius: 11))
                .overlay(RoundedRectangle(cornerRadius: 11).stroke(Color.labLine))
            Button("Calculate paper rebalance") { Task { await store.calculateRebalance(rebalanceTargets) } }
                .buttonStyle(LabButtonStyle())
                .disabled(rebalanceTargets.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            if let result = store.rebalanceResult {
                ForEach(result.rows) { row in
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("\(row.symbol) · \(row.targetPercent)%").font(.subheadline.weight(.semibold))
                            Text("\(row.currentValue.currency) → \(row.targetValue.currency) at \(row.referencePrice.currency)")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text("\(row.shareAdjustment.decimal > 0 ? "+" : "")\(row.shareAdjustment) shares")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(row.shareAdjustment.decimal < 0 ? Color.red : Color.labGreen)
                    }
                }
                Text("\(result.cashTargetPercent)% cash target. \(labLocalized(result.disclaimer))")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private var priceAlerts: some View {
        LabSection(
            title: "Price alerts",
            subtitle: "Threshold crossings from cached daily closes.",
            badge: "END OF DAY"
        ) {
            HStack(spacing: 10) {
                LabMetricCard(
                    label: "Active rules",
                    value: "\(store.snapshot.alerts.rules.count)",
                    detail: "Up to 50"
                )
                LabMetricCard(
                    label: "Trigger history",
                    value: "\(store.snapshot.alerts.recentTriggers.count)",
                    detail: "Synced crossings"
                )
            }
            if store.snapshot.alerts.rules.isEmpty {
                LabEmptyLine(text: "Add a threshold from the plus menu.")
            } else {
                ForEach(store.snapshot.alerts.rules) { rule in
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(spacing: 7) {
                                Text(rule.symbol).font(.headline)
                                Text(rule.isTriggered ? "MET" : "WATCHING")
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundStyle(rule.isTriggered ? Color.signalOrange : .secondary)
                            }
                            Text("\(rule.direction == "above" ? "At or above" : "At or below") \(rule.threshold.currency)")
                                .font(.caption.monospacedDigit())
                            Text(rule.latestPrice.map { "Latest \($0.currency) · \(rule.tradingDate ?? "")" } ?? "Waiting for a cached close")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button(role: .destructive) {
                            Task { await store.deletePriceAlert(rule.id) }
                        } label: {
                            Image(systemName: "trash")
                        }
                        .accessibilityLabel("Remove \(rule.symbol) price alert")
                    }
                    .padding(.vertical, 6)
                }
            }
            if let trigger = store.snapshot.alerts.recentTriggers.first {
                Divider()
                Text("Latest crossing")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.secondary)
                Text("\(trigger.symbol) · \(trigger.observedPrice.currency) on \(trigger.tradingDate)")
                    .font(.subheadline.monospacedDigit().weight(.semibold))
                Text(trigger.triggeredAt)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text(labLocalized(store.snapshot.alerts.disclaimer))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

private struct HeaderMetric: View {
    let label: String
    let value: String
    var color: Color = .white

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(LocalizedStringKey(label))
                .textCase(.uppercase)
                .font(.system(size: 11, weight: .bold))
                .tracking(0.8)
                .foregroundStyle(Color.white.opacity(0.68))
            Text(value)
                .font(.subheadline.monospacedDigit().weight(.bold))
                .foregroundStyle(color)
                .lineLimit(1)
                .minimumScaleFactor(0.85)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct DayTradeWorkspace: View {
    @ObservedObject var store: LabStore
    @State private var symbol = ""
    @State private var direction = "long"
    @State private var accountSize = "25000"
    @State private var riskPercent = "0.5"
    @State private var maxPositionPercent = "10"
    @State private var entry = ""
    @State private var stop = ""
    @State private var target = ""
    @State private var minimumRewardRisk = "2"
    @State private var dailyLossLimit = "300"
    @State private var currentDailyLoss = "0"
    @State private var hypothesis = ""
    @State private var alpacaKeyID = ""
    @State private var alpacaSecret = ""
    @State private var setupKey = "manual"
    @State private var liveMonitorEnabled = false
    @State private var replayDate = ""
    @State private var replayIndex = 0.0

    private var latest: ResearchPlan? {
        store.snapshot.recentPlans.first { $0.kind == "day_trade" }
    }

    var body: some View {
        LabGuardrailCard(
            eyebrow: "INTRADAY WORKSPACE",
            title: "Plan the exit before the entry.",
            message: "Combine your own entry and invalidation with IEX real-time levels, Nasdaq halt status, and app-ledger stop conditions. Live-account orders remain blocked; Paper orders require Command acknowledgement."
        )
        LabSection(
            title: "Watchlist day-trade scanner",
            subtitle: "Gap, relative volume, IEX spread, risk gates, and deterministic setups.",
            badge: store.dayTradeScanner?.alertCandidates.isEmpty == false ? "READY" : "PAPER ONLY"
        ) {
            Button("Scan watchlist") { Task { await store.loadDayTradeScanner() } }
                .buttonStyle(LabButtonStyle())
            if let scanner = store.dayTradeScanner {
                Text("\(scanner.marketClock.sessionPhase.uppercased()) · \(scanner.marketClock.source)")
                    .font(.caption).foregroundStyle(.secondary)
                if scanner.rows.isEmpty {
                    LabEmptyLine(text: scanner.errors.first?.error ?? "No watchlist symbol returned live data.")
                } else {
                    ForEach(scanner.rows) { row in
                        HStack(spacing: 10) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(row.symbol).font(.subheadline.weight(.semibold))
                                Text("Gap \(row.gapPercent ?? "—")% · RVOL \(row.relativeVolume ?? "—")x · spread \(row.spreadPercent ?? "—")%")
                                    .font(.caption2).foregroundStyle(.secondary)
                                Text(row.bestSetup.map { "\(labLocalized($0.label)) · \(labLocalized($0.status)) · \($0.score)/100" } ?? "No setup")
                                    .font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if let best = row.bestSetup {
                                Button("Open") {
                                    symbol = row.symbol
                                    Task {
                                        await store.loadRealtimeDayPlan(row.symbol)
                                        if let liveSetup = store.realtimeDayPlan?.setups?.first(where: { $0.key == best.key }) {
                                            apply(liveSetup)
                                        }
                                    }
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                    }
                }
                Text(labLocalized(scanner.scope)).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .id("day-scanner")
        LabSection(
            title: "Live premarket plan",
            subtitle: "Alpaca Basic IEX observations plus Nasdaq current trade halts.",
            badge: store.marketStatus?.realtime.configured == true ? "IEX LIVE" : "SETUP"
        ) {
            if store.marketStatus?.realtime.configured != true {
                SecureField("Alpaca API key ID", text: $alpacaKeyID).labAuthField()
                SecureField("Alpaca secret key", text: $alpacaSecret).labAuthField()
                Button("Save to Mac Keychain") {
                    Task {
                        if await store.configureRealtime(keyID: alpacaKeyID, secret: alpacaSecret) {
                            alpacaKeyID = ""; alpacaSecret = ""
                        }
                    }
                }
                .buttonStyle(.bordered)
            }
            HStack {
                PlanningField("Symbol", text: $symbol, keyboard: .default, uppercase: true)
                Button("Load live plan") { Task { await store.loadRealtimeDayPlan(symbol) } }
                    .buttonStyle(LabButtonStyle(compact: true))
                    .disabled(symbol.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            Toggle("Live monitor · refresh every 20 seconds", isOn: $liveMonitorEnabled)
                .disabled(symbol.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            if let live = store.realtimeDayPlan, live.available {
                HStack(spacing: 10) {
                    LabMetricCard(label: "Latest", value: (live.latestPrice ?? "0").currency, detail: live.spread.map { "Spread \($0)" } ?? "IEX trade")
                    LabMetricCard(label: "VWAP", value: live.vwap?.currency ?? "—", detail: "Relative volume \(live.relativeVolume ?? "—")x")
                }
                HStack(spacing: 10) {
                    LabMetricCard(label: "Premarket H / L", value: live.premarketHigh.map { "\($0.currency) / \((live.premarketLow ?? "0").currency)" } ?? "—", detail: "Observed IEX bars")
                    LabMetricCard(label: "Opening range", value: live.openingRangeHigh.map { "\($0.currency) / \((live.openingRangeLow ?? "0").currency)" } ?? "—", detail: "First five minutes")
                }
                HStack(spacing: 10) {
                    LabMetricCard(label: "Support", value: live.support?.currency ?? "—", detail: "Observed level")
                    LabMetricCard(label: "Resistance", value: live.resistance?.currency ?? "—", detail: "Observed level")
                }
                Text(live.halt.halted ? "HALTED · \(live.halt.reasonCode ?? "reason pending")" : "Nasdaq halt check: clear")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(live.halt.halted ? Color.red : Color.labGreen)
                if let setups = live.setups, !setups.isEmpty {
                    Text("RULE-BASED SETUPS").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                    ForEach(setups) { setup in
                        VStack(alignment: .leading, spacing: 5) {
                            HStack {
                                Text(labLocalized(setup.label)).font(.caption.weight(.semibold))
                                Spacer()
                                Text("\(setup.score) / 100 · \(labLocalized(setup.status))")
                                    .font(.caption2.monospacedDigit())
                            }
                            Text("\(labLocalized(setup.direction ?? "Waiting")) · \(setup.entry?.currency ?? "—") / \(setup.stop?.currency ?? "—") → \(setup.target?.currency ?? "—")")
                                .font(.caption2).foregroundStyle(.secondary)
                            if setup.entry != nil && setup.stop != nil && setup.target != nil {
                                Button("Use paper worksheet") { apply(setup) }
                                    .buttonStyle(.bordered)
                            }
                        }
                        .padding(10)
                        .background(Color.labPaper, in: RoundedRectangle(cornerRadius: 11))
                    }
                }
                if let replay = live.replay {
                    HStack(spacing: 10) {
                        LabMetricCard(label: "Replay sessions", value: "\(replay.sessions)", detail: "\(replay.triggeredSessions) triggered")
                        LabMetricCard(label: "Target hit rate", value: replay.targetHitRatePercent.map { "\($0)%" } ?? "—", detail: "\(replay.averageRMultiple ?? "—")R average")
                    }
                    Text(labLocalized(replay.scope)).font(.caption2).foregroundStyle(.secondary)
                }
                Text(labLocalized(live.dataScope)).font(.caption2).foregroundStyle(.secondary)
            } else if let live = store.realtimeDayPlan {
                LabEmptyLine(text: live.reason ?? "Live plan is unavailable.")
            }
            Divider()
            Button("Load minute-by-minute replay") {
                Task {
                    await store.loadDayTradeReplay(symbol)
                    replayDate = store.dayTradeSessionReplay?.sessionDate ?? ""
                    replayIndex = 0
                }
            }
            .buttonStyle(.bordered)
            .disabled(symbol.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            if let replay = store.dayTradeSessionReplay {
                if !replay.availableDates.isEmpty {
                    Picker("Replay session", selection: $replayDate) {
                        ForEach(replay.availableDates, id: \.self) { Text($0).tag($0) }
                    }
                    .onChange(of: replayDate) { _, value in
                        guard !value.isEmpty else { return }
                        replayIndex = 0
                        Task { await store.loadDayTradeReplay(symbol, date: value) }
                    }
                }
                if replay.available, let bars = replay.bars, !bars.isEmpty {
                    let selectedIndex = min(max(Int(replayIndex), 0), bars.count - 1)
                    let bar = bars[selectedIndex]
                    Text("\(replay.sessionDate ?? "") · \(labLocalized(replay.direction ?? "No trigger")) · \(labLocalized(replay.outcome ?? "open")) · \(replay.realizedRMultiple ?? "—")R")
                        .font(.caption.weight(.semibold))
                    Slider(value: $replayIndex, in: 0...Double(max(1, bars.count - 1)), step: 1)
                    HStack(spacing: 10) {
                        LabMetricCard(label: "Replay time", value: String(bar.timestamp.suffix(14).prefix(8)), detail: "Bar \(selectedIndex + 1) / \(bars.count)")
                        LabMetricCard(label: "Close", value: bar.close.currency, detail: "H \(bar.high.currency) · L \(bar.low.currency)")
                    }
                    Text("Opening range \((replay.openingRangeHigh ?? "0").currency) / \((replay.openingRangeLow ?? "0").currency) · entry \((replay.entry ?? "0").currency) · stop \((replay.stop ?? "0").currency) · target \((replay.target ?? "0").currency)")
                        .font(.caption2).foregroundStyle(.secondary)
                } else {
                    LabEmptyLine(text: replay.reason ?? "No complete cached replay is available.")
                }
            }
        }
        .id("day-live")
        let guardrails = store.snapshot.dayTradeGuardrails
        LabSection(
            title: "PDT transition & daily stop monitor",
            subtitle: guardrails.scope,
            badge: guardrails.stopTriggered ? "STOP" : guardrails.pdtThresholdReached ? "PDT REVIEW" : "CLEAR"
        ) {
            HStack(spacing: 10) {
                LabMetricCard(label: "Estimated day trades", value: "\(guardrails.estimatedDayTrades)", detail: "\(guardrails.windowStart) to \(guardrails.windowEnd)")
                LabMetricCard(label: "Consecutive losses", value: "\(guardrails.consecutiveLosses)", detail: "Self-recorded reviews")
            }
            LabMetricCard(label: "Recorded loss today", value: guardrails.recordedLossToday.currency, detail: "\(guardrails.dailyLossLimit.currency) saved limit")
            if guardrails.stopConditions.isEmpty {
                LabEmptyLine(text: "No app-recorded stop condition is active.")
            } else {
                ForEach(guardrails.stopConditions, id: \.self) { condition in
                    Label(labLocalized(condition), systemImage: "exclamationmark.octagon.fill")
                        .font(.caption).foregroundStyle(Color.red)
                }
            }
        }
        .id("day-guardrails")
        LabSection(title: "Manual risk worksheet", subtitle: "Share capacity comes only from the limits you enter.", badge: "NO LIVE ORDER") {
            Picker("Direction", selection: $direction) {
                Text("Long").tag("long")
                Text("Short").tag("short")
            }
            .pickerStyle(.segmented)
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                PlanningField("Symbol", text: $symbol, keyboard: .default, uppercase: true)
                PlanningField("Account size", text: $accountSize)
                PlanningField("Entry", text: $entry)
                PlanningField("Stop", text: $stop)
                PlanningField("Target", text: $target)
                PlanningField("Risk %", text: $riskPercent)
                PlanningField("Maximum position %", text: $maxPositionPercent)
                PlanningField("Daily loss cap", text: $dailyLossLimit)
                PlanningField("Loss already used", text: $currentDailyLoss)
                PlanningField("Minimum R", text: $minimumRewardRisk)
            }
            TextField("Neutral hypothesis", text: $hypothesis, axis: .vertical)
                .lineLimit(2...5)
                .padding(12)
                .background(Color.white, in: RoundedRectangle(cornerRadius: 11))
                .overlay(RoundedRectangle(cornerRadius: 11).stroke(Color.labLine))
            Button("Save planning worksheet") {
                Task {
                    await store.recordDayPlan(
                        symbol: symbol,
                        direction: direction,
                        hypothesis: hypothesis,
                        accountSize: accountSize,
                        entry: entry,
                        stop: stop,
                        target: target,
                        riskPercent: riskPercent,
                        maxPositionPercent: maxPositionPercent,
                        dailyLossLimit: dailyLossLimit,
                        currentDailyLoss: currentDailyLoss,
                        minimumRewardRisk: minimumRewardRisk,
                        setupKey: setupKey,
                        live: store.realtimeDayPlan
                    )
                }
            }
            .buttonStyle(LabButtonStyle())
            .disabled(symbol.isEmpty || entry.isEmpty || stop.isEmpty || target.isEmpty || hypothesis.isEmpty)
            if let analysis = latest?.analysis {
                HStack(spacing: 10) {
                    LabMetricCard(label: "Risk budget", value: (analysis.effectiveRiskBudget ?? "0").currency, detail: "Your limits")
                    LabMetricCard(label: "Share ceiling", value: "\(analysis.maximumWholeShares ?? 0)", detail: analysis.bindingConstraintLabel)
                }
                HStack(spacing: 10) {
                    LabMetricCard(label: "Reward / risk", value: "\(analysis.rewardRisk ?? "—")R", detail: analysis.meetsRewardRiskFloor == true ? "Meets your floor" : "Review your floor")
                    LabMetricCard(label: "Status", value: analysis.planStatus == "blocked" ? "Blocked" : "Review", detail: "Manual only")
                }
                Text("\(labLocalized(analysis.dataFreshness)) \(labLocalized(analysis.disclaimer))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .id("day-worksheet")
        .onAppear {
            if symbol.isEmpty { symbol = store.snapshot.watchlist.first?.symbol ?? "" }
            applyPlanningDefaults()
        }
        .onChange(of: store.snapshot.investorProfile.updatedAt) { _, _ in applyPlanningDefaults() }
        .task(id: liveMonitorEnabled) {
            guard liveMonitorEnabled else { return }
            while liveMonitorEnabled && !Task.isCancelled {
                await store.loadRealtimeDayPlan(symbol)
                do { try await Task.sleep(nanoseconds: 20_000_000_000) }
                catch { break }
            }
        }
    }

    private func applyPlanningDefaults() {
        let profile = store.snapshot.investorProfile
        accountSize = profile.paperAccountSize
        riskPercent = profile.riskPerTradePercent
        maxPositionPercent = profile.maxPositionPercent
        minimumRewardRisk = profile.minimumRewardRisk
        dailyLossLimit = profile.dailyLossLimit
    }

    private func apply(_ setup: DayTradeSetup) {
        guard let setupEntry = setup.entry, let setupStop = setup.stop,
              let setupTarget = setup.target, let setupDirection = setup.direction else { return }
        setupKey = setup.key
        direction = setupDirection
        entry = setupEntry
        stop = setupStop
        target = setupTarget
        hypothesis = "\(setup.label): review only while the observed trigger remains valid and the saved risk gate is clear."
    }
}

private struct OptionsWorkspace: View {
    @ObservedObject var store: LabStore
    @State private var symbol = ""
    @State private var strategy = "long_call"
    @State private var expiration = Calendar.current.date(byAdding: .day, value: 30, to: Date()) ?? Date()
    @State private var quantity = "1"
    @State private var primaryStrike = ""
    @State private var primaryPremium = ""
    @State private var secondaryStrike = ""
    @State private var secondaryPremium = ""
    @State private var tertiaryStrike = ""
    @State private var tertiaryPremium = ""
    @State private var quaternaryStrike = ""
    @State private var quaternaryPremium = ""
    @State private var hypothesis = ""
    @State private var chainRight = "all"
    @State private var minimumDTE = "7"
    @State private var maximumDTE = "60"
    @State private var minimumVolume = "1"
    @State private var maximumSpread = "20"

    private var requiresSecondLeg: Bool {
        ["bull_call_spread", "bear_put_spread", "long_straddle", "iron_condor"].contains(strategy)
    }
    private var isFourLeg: Bool { strategy == "iron_condor" }
    private var latest: ResearchPlan? {
        store.snapshot.recentPlans.first { $0.kind == "options" }
    }

    var body: some View {
        LabGuardrailCard(
            eyebrow: "OPTIONS LABORATORY",
            title: "Structure first. Premium second.",
            message: "Inspect indicative chain snapshots, Greeks, IV, and liquidity before comparing defined-risk expiration payoff. Execution remains disabled."
        )
        LabSection(
            title: "Option chain explorer",
            subtitle: "Indicative snapshots, expected move, liquidity gates, and candidate structures.",
            badge: "INDICATIVE"
        ) {
            HStack {
                PlanningField("Underlying symbol", text: $symbol, keyboard: .default, uppercase: true)
                Button("Load chain") { Task { await store.loadOptionChain(symbol, right: chainRight, minimumDTE: minimumDTE, maximumDTE: maximumDTE, minimumVolume: minimumVolume, maximumSpread: maximumSpread) } }
                    .buttonStyle(LabButtonStyle(compact: true))
                    .disabled(symbol.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            Picker("Contract side", selection: $chainRight) {
                Text("Calls and puts").tag("all")
                Text("Calls").tag("call")
                Text("Puts").tag("put")
            }
            .pickerStyle(.segmented)
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                PlanningField("Minimum DTE", text: $minimumDTE, keyboard: .numberPad)
                PlanningField("Maximum DTE", text: $maximumDTE, keyboard: .numberPad)
                PlanningField("Minimum volume", text: $minimumVolume, keyboard: .numberPad)
                PlanningField("Maximum spread %", text: $maximumSpread)
            }
            if let chain = store.optionChain, chain.available, let summary = chain.summary {
                HStack(spacing: 10) {
                    LabMetricCard(label: "Underlying", value: chain.underlyingPrice?.currency ?? "—", detail: "\(summary.contracts) contracts")
                    LabMetricCard(label: "ATM implied volatility", value: summary.atmIVPercent.map { "\($0)%" } ?? "—", detail: summary.ivPercentile.map { "\($0)% percentile" } ?? "Building history")
                }
                HStack(spacing: 10) {
                    LabMetricCard(label: "Expected move", value: summary.expectedMove.map { "±\($0.currency)" } ?? "—", detail: "\(summary.expectedMoveDays ?? 0) calendar days")
                    LabMetricCard(label: "Liquidity gate", value: "\(summary.liquidContracts)", detail: "\(summary.expirations) expirations")
                }
                if let analytics = chain.analytics {
                    LabMetricCard(
                        label: "Term structure",
                        value: "\(analytics.termStructure.count) expirations",
                        detail: analytics.termStructure.prefix(4).map { "\($0.daysToExpiration)D \($0.atmIVPercent)%" }.joined(separator: " · ")
                    )
                    HStack(spacing: 10) {
                        LabMetricCard(label: "Paper delta", value: analytics.portfolioGreeks.deltaShares, detail: "\(analytics.portfolioGreeks.matchedPositions) matched positions")
                        LabMetricCard(label: "Theta / day", value: analytics.portfolioGreeks.thetaPerDay, detail: "Indicative snapshot Greeks")
                    }
                }
                ForEach(chain.candidates ?? []) { candidate in
                    VStack(alignment: .leading, spacing: 5) {
                        HStack {
                            Text(labLocalized(candidate.label)).font(.caption.weight(.semibold))
                            Spacer()
                            Text(candidate.expiration).font(.caption2.monospacedDigit())
                        }
                        Text(candidate.legs.map { "\(labLocalized($0.action)) \($0.strike) \(labLocalized($0.right))" }.joined(separator: " / "))
                            .font(.caption2).foregroundStyle(.secondary)
                        Text("\(candidate.maximumLossPerContract.currency) max loss · \(candidate.breakeven) breakeven")
                            .font(.caption2).foregroundStyle(.secondary)
                        Button("Use paper worksheet") { apply(candidate, chain: chain) }
                            .buttonStyle(.bordered)
                    }
                    .padding(10)
                    .background(Color.labPaper, in: RoundedRectangle(cornerRadius: 11))
                }
                Text("LIQUID CONTRACTS").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                ForEach((chain.contracts ?? []).filter(\.liquid).prefix(20)) { contract in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(contract.expiration) · \(labLocalized(contract.right)) · \(contract.strike.currency)")
                                .font(.caption.weight(.semibold))
                            Text("IV \(contract.impliedVolatilityPercent ?? "—")% · Δ \(contract.delta.map { String(format: "%.2f", $0) } ?? "—") · volume \(contract.volume)")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text("\(contract.bid.currency) / \(contract.ask.currency)")
                            .font(.caption.monospacedDigit())
                    }
                }
                Text(labLocalized(chain.dataScope)).font(.caption2).foregroundStyle(.secondary)
            } else if let chain = store.optionChain {
                LabEmptyLine(text: chain.reason ?? "Option chain is unavailable.")
            }
        }
        .id("options-chain")
        LabSection(title: "Expiration payoff", subtitle: "Deterministic contract math using manual premiums.", badge: "MANUAL DATA") {
            Picker("Strategy", selection: $strategy) {
                Text("Long call").tag("long_call")
                Text("Long put").tag("long_put")
                Text("Bull call").tag("bull_call_spread")
                Text("Bear put").tag("bear_put_spread")
                Text("Long straddle / strangle").tag("long_straddle")
                Text("Iron condor").tag("iron_condor")
            }
            .pickerStyle(.menu)
            DatePicker("Expiration", selection: $expiration, in: Date()..., displayedComponents: .date)
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                PlanningField("Symbol", text: $symbol, keyboard: .default, uppercase: true)
                PlanningField("Contracts", text: $quantity)
                PlanningField(strategy == "iron_condor" ? "Long put strike" : strategy == "long_straddle" ? "Call strike" : requiresSecondLeg ? "Long strike" : "Strike", text: $primaryStrike)
                PlanningField(strategy == "iron_condor" ? "Long put premium" : strategy == "long_straddle" ? "Call premium" : requiresSecondLeg ? "Long premium" : "Premium paid", text: $primaryPremium)
                if requiresSecondLeg {
                    PlanningField(strategy == "iron_condor" ? "Short put strike" : strategy == "long_straddle" ? "Put strike" : "Short strike", text: $secondaryStrike)
                    PlanningField(strategy == "iron_condor" ? "Short put premium" : strategy == "long_straddle" ? "Put premium" : "Short premium", text: $secondaryPremium)
                }
                if isFourLeg {
                    PlanningField("Short call strike", text: $tertiaryStrike)
                    PlanningField("Short call premium", text: $tertiaryPremium)
                    PlanningField("Long call strike", text: $quaternaryStrike)
                    PlanningField("Long call premium", text: $quaternaryPremium)
                }
            }
            TextField("Neutral hypothesis", text: $hypothesis, axis: .vertical)
                .lineLimit(2...5)
                .padding(12)
                .background(Color.white, in: RoundedRectangle(cornerRadius: 11))
                .overlay(RoundedRectangle(cornerRadius: 11).stroke(Color.labLine))
            Button("Save payoff worksheet") {
                Task {
                    await store.recordOptionPlan(
                        symbol: symbol,
                        strategy: strategy,
                        hypothesis: hypothesis,
                        expiration: expiration,
                        quantity: quantity,
                        primaryStrike: primaryStrike,
                        primaryPremium: primaryPremium,
                        secondaryStrike: secondaryStrike,
                        secondaryPremium: secondaryPremium,
                        tertiaryStrike: tertiaryStrike,
                        tertiaryPremium: tertiaryPremium,
                        quaternaryStrike: quaternaryStrike,
                        quaternaryPremium: quaternaryPremium
                    )
                }
            }
            .buttonStyle(LabButtonStyle())
            .disabled(symbol.isEmpty || primaryStrike.isEmpty || primaryPremium.isEmpty || hypothesis.isEmpty || (requiresSecondLeg && (secondaryStrike.isEmpty || secondaryPremium.isEmpty)) || (isFourLeg && (tertiaryStrike.isEmpty || tertiaryPremium.isEmpty || quaternaryStrike.isEmpty || quaternaryPremium.isEmpty)))
            if let analysis = latest?.analysis {
                HStack(spacing: 10) {
                    LabMetricCard(label: "Maximum loss", value: (analysis.maxLoss ?? "0").currency, detail: "At expiration")
                    LabMetricCard(label: "Maximum profit", value: analysis.maxProfit.map { $0.currency } ?? analysis.maxProfitLabel ?? "—", detail: "At expiration")
                }
                HStack(spacing: 10) {
                    LabMetricCard(label: "Breakeven", value: (analysis.breakevens ?? [analysis.breakeven ?? "0"]).map(\.currency).joined(separator: " / "), detail: "At expiration")
                    LabMetricCard(label: analysis.netPremiumLabel?.capitalized ?? "Net debit", value: abs((analysis.netDebit ?? "0").decimal).description.currency, detail: "Manual premium")
                }
                Text("\(labLocalized(analysis.dataFreshness)) \(labLocalized(analysis.disclaimer))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .id("options-payoff")
        .onAppear {
            if symbol.isEmpty { symbol = store.snapshot.watchlist.first?.symbol ?? "" }
            applyPlanningDefaults()
        }
        .onChange(of: store.snapshot.investorProfile.updatedAt) { _, _ in applyPlanningDefaults() }
    }

    private func applyPlanningDefaults() {
        if store.snapshot.investorProfile.optionsDefinedRiskOnly,
           strategy == "long_call" || strategy == "long_put" {
            strategy = "bull_call_spread"
        }
    }

    private func apply(_ candidate: OptionCandidate, chain: OptionChain) {
        let contracts = Dictionary(uniqueKeysWithValues: (chain.contracts ?? []).map { ($0.contractSymbol, $0) })
        let quoted = candidate.legs.compactMap { leg -> (OptionCandidateLeg, OptionContract)? in
            contracts[leg.contractSymbol].map { (leg, $0) }
        }
        guard quoted.count == candidate.legs.count, let first = quoted.first else { return }
        symbol = chain.symbol
        strategy = candidate.strategy
        if let parsed = ISO8601DateFormatter().date(from: "\(candidate.expiration)T12:00:00Z") { expiration = parsed }
        let values = quoted.map { leg, contract in
            (leg.strike, leg.action == "buy" ? contract.ask : contract.bid)
        }
        primaryStrike = first.0.strike
        primaryPremium = first.0.action == "buy" ? first.1.ask : first.1.bid
        secondaryStrike = values.indices.contains(1) ? values[1].0 : ""
        secondaryPremium = values.indices.contains(1) ? values[1].1 : ""
        tertiaryStrike = values.indices.contains(2) ? values[2].0 : ""
        tertiaryPremium = values.indices.contains(2) ? values[2].1 : ""
        quaternaryStrike = values.indices.contains(3) ? values[3].0 : ""
        quaternaryPremium = values.indices.contains(3) ? values[3].1 : ""
        hypothesis = "\(candidate.label): compare the defined expiration payoff after re-checking liquidity and event risk."
    }
}

private struct CommandCenterView: View {
    @ObservedObject var store: LabStore
    @AppStorage("workflowSymbol") private var workflowSymbol = ""
    @State private var paperEnabled = false
    @State private var paperMaximum = "1000"
    @State private var paperDailyStop = "300"
    @State private var controlAcknowledged = false
    @State private var orderSymbol = ""
    @State private var orderSide = "buy"
    @State private var orderType = "limit"
    @State private var orderQuantity = "1"
    @State private var orderLimit = ""
    @State private var orderStop = ""
    @State private var orderAcknowledged = false
    @State private var scannerName = "My cached screen"
    @State private var scannerSymbols = ""
    @State private var scannerScore = "0"
    @State private var ruleKind = "decision"
    @State private var ruleSymbol = ""
    @State private var ruleThreshold = "7"
    @State private var optionSpot = "200"
    @State private var optionDays = "30"
    @State private var optionLongStrike = "195"
    @State private var optionLongPremium = "8"
    @State private var optionShortStrike = "210"
    @State private var optionShortPremium = "3"
    @State private var comparisonSymbol = ""
    @State private var copilotSymbol = ""
    @State private var copilotQuestion = "What evidence supports and challenges this setup?"
    @State private var showAdvancedTools = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    commandHeader
                    paperExecution
                    Button {
                        withAnimation(.easeInOut(duration: 0.2)) { showAdvancedTools.toggle() }
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(showAdvancedTools ? "Hide advanced tools" : "Show advanced tools")
                                    .font(.subheadline.weight(.semibold))
                                Text("Scanners, alerts, option scenarios, comparisons, reports, and data quality.")
                                    .font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Image(systemName: showAdvancedTools ? "chevron.up" : "chevron.down")
                                .foregroundStyle(Color.signalOrange)
                        }
                        .padding(14)
                        .background(Color.white, in: RoundedRectangle(cornerRadius: 15))
                        .overlay(RoundedRectangle(cornerRadius: 15).stroke(Color.labLine))
                    }
                    .buttonStyle(.plain)
                    if showAdvancedTools {
                        universeScanner
                        notificationRules
                        optionScenario
                        researchAndPortfolio
                        researchCopilot
                        reportsAndQuality
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 18)
            }
            .background(Color.labPaper.ignoresSafeArea())
            .navigationTitle("Command")
            .navigationBarTitleDisplayMode(.inline)
            .refreshable { await store.loadCommandCenter() }
            .onAppear {
                loadControl()
                if orderSymbol.isEmpty { orderSymbol = workflowSymbol }
            }
            .onChange(of: store.researchCommandCenter?.paperExecution.updatedAt) { _, _ in loadControl() }
        }
    }

    private var commandHeader: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("RESEARCH OPERATING SYSTEM")
                .font(.caption.weight(.bold)).tracking(1.2).foregroundStyle(Color.signalOrange)
            Text("One loop from signal to review.")
                .font(.system(size: 31, weight: .semibold)).tracking(-0.7).foregroundStyle(.white)
            Text("Cached scanner, Alpaca Paper execution, evidence review, and synchronized reporting.")
                .font(.subheadline).foregroundStyle(.white.opacity(0.76))
            HStack {
                Label("Paper endpoint", systemImage: "checkmark.shield.fill")
                Spacer()
                Text(store.researchCommandCenter?.paperExecution.enabled == true ? "ENABLED" : "LOCKED")
                    .font(.caption.weight(.bold)).foregroundStyle(Color.labMint)
            }
            .font(.caption).foregroundStyle(.white)
        }
        .padding(22)
        .background(
            LinearGradient(colors: [.labInk, .labInkSoft], startPoint: .topLeading, endPoint: .bottomTrailing),
            in: RoundedRectangle(cornerRadius: 27, style: .continuous)
        )
    }

    private var paperExecution: some View {
        LabSection(
            title: "Alpaca Paper execution gate",
            subtitle: store.currentUser?.role == "owner"
                ? "Real-account routing is not implemented. The server checks acknowledgement, order value, daily loss, position size, and duplicate IDs."
                : "Paper account routing and shared provider controls are available only to the workspace owner.",
            badge: store.currentUser?.role == "owner" ? (paperEnabled ? "ENABLED" : "LOCKED") : "OWNER ONLY"
        ) {
            Toggle("Enable Alpaca Paper routing", isOn: $paperEnabled)
            HStack(spacing: 10) {
                PlanningField("Order cap", text: $paperMaximum)
                PlanningField("Daily loss stop", text: $paperDailyStop)
            }
            if paperEnabled {
                Toggle(
                    "I understand this route is limited to Alpaca Paper and cannot reach a live account.",
                    isOn: $controlAcknowledged
                )
            }
            Button("Save execution gate") {
                Task {
                    if await store.updatePaperExecution(
                        enabled: paperEnabled, maximum: paperMaximum,
                        dailyStop: paperDailyStop, acknowledged: controlAcknowledged
                    ) { controlAcknowledged = false }
                }
            }
            .buttonStyle(LabButtonStyle())
            .disabled(store.isLoading || (paperEnabled && !controlAcknowledged))
            Divider()
            Text("PAPER ORDER").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
            PlanningField("Symbol", text: $orderSymbol, keyboard: .default)
            HStack {
                Picker("Side", selection: $orderSide) {
                    Text("Buy").tag("buy")
                    Text("Sell held shares").tag("sell")
                }
                Picker("Order type", selection: $orderType) {
                    Text("Limit").tag("limit")
                    Text("Market").tag("market")
                    Text("Stop").tag("stop")
                    Text("Stop limit").tag("stop_limit")
                }
            }
            PlanningField("Quantity", text: $orderQuantity)
            if ["limit", "stop_limit"].contains(orderType) {
                PlanningField("Limit price", text: $orderLimit)
            }
            if ["stop", "stop_limit"].contains(orderType) {
                PlanningField("Stop price", text: $orderStop)
            }
            Toggle(
                "I reviewed the symbol, side, quantity, and prices for this simulated order.",
                isOn: $orderAcknowledged
            )
            Button("Submit paper order") {
                Task {
                    if await store.submitPaperOrder(
                        symbol: orderSymbol, side: orderSide, orderType: orderType,
                        quantity: orderQuantity, limitPrice: orderLimit,
                        stopPrice: orderStop, acknowledged: orderAcknowledged
                    ) { orderAcknowledged = false }
                }
            }
            .buttonStyle(LabButtonStyle())
            .disabled(store.isLoading || orderSymbol.isEmpty || !orderAcknowledged)
            ForEach((store.paperOrderLedger?.orders ?? []).prefix(12)) { order in
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("\(order.symbol) · \(labLocalized(order.side))").font(.subheadline.weight(.semibold))
                        Text("\(order.orderType) · \(order.quantity) · \(order.estimatedNotional.currency)")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 4) {
                        Text(order.status.uppercased()).font(.caption2.weight(.bold))
                        if order.isCancelable {
                            Button("Cancel") { Task { await store.cancelPaperOrder(order) } }
                                .buttonStyle(.bordered).controlSize(.small)
                        }
                    }
                }
                .padding(.vertical, 3)
            }
        }
        .disabled(store.currentUser?.role != "owner")
    }

    private var universeScanner: some View {
        LabSection(
            title: "Cached universe scanner",
            subtitle: "Screen up to 250 cached symbols without a paid scan request.",
            badge: "COST CONTROLLED"
        ) {
            PlanningField("Preset name", text: $scannerName, keyboard: .default)
            PlanningField("Symbols · comma separated", text: $scannerSymbols, keyboard: .default)
            PlanningField("Minimum score", text: $scannerScore)
            Button("Save and run screen") {
                Task { await store.runUniverseScanner(name: scannerName, symbols: scannerSymbols, minimumScore: scannerScore) }
            }
            .buttonStyle(LabButtonStyle()).disabled(store.isLoading || scannerName.isEmpty)
            if let scan = store.universeScan {
                Text("\(scan.matched) matched / \(scan.universeSize) cached")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(scan.rows.prefix(20)) { item in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.symbol).font(.subheadline.weight(.semibold))
                            Text("\(labLocalized(item.signalLabel)) · \(item.freshness)")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(item.score.map(String.init) ?? "—").font(.headline.monospacedDigit())
                        Text(item.close.currency).font(.caption.monospacedDigit())
                    }
                }
            }
        }
    }

    private var notificationRules: some View {
        LabSection(
            title: "Unified notification rules",
            subtitle: "Decision, filing, earnings, expiration, Day Trade, and stale-data rules use synchronized evidence.",
            badge: "LOCAL"
        ) {
            Picker("Rule type", selection: $ruleKind) {
                Text("Decision").tag("decision")
                Text("SEC filing").tag("filing")
                Text("Earnings").tag("earnings")
                Text("Option expiration").tag("option_expiration")
                Text("Day Trade").tag("day_trade")
                Text("Data stale").tag("data_stale")
            }
            PlanningField("Symbol · optional", text: $ruleSymbol, keyboard: .default)
            PlanningField("Threshold", text: $ruleThreshold)
            Button("Add notification rule") {
                Task { await store.addNotificationRule(kind: ruleKind, symbol: ruleSymbol, threshold: ruleThreshold) }
            }
            .buttonStyle(.bordered).disabled(store.isLoading)
            ForEach(store.notificationRuleCenter?.operationalAlerts ?? []) { alert in
                HStack(alignment: .top, spacing: 9) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(Color.signalOrange)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(labLocalized(alert.kind.replacingOccurrences(of: "_", with: " ")))
                            .font(.subheadline.weight(.semibold))
                        Text(labLocalized(alert.detail))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            ForEach(store.notificationRuleCenter?.rules ?? []) { rule in
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(labLocalized(rule.kind.replacingOccurrences(of: "_", with: " ")))
                            .font(.subheadline.weight(.semibold))
                        Text("\(rule.symbol ?? String(localized: "All symbols")) · \(labLocalized(rule.detail))")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text(rule.active ? "ACTIVE" : "WAITING").font(.caption2.weight(.bold))
                }
            }
        }
    }

    private var optionScenario: some View {
        LabSection(
            title: "Flexible option scenario",
            subtitle: "This compact builder models a two-leg call spread; the server accepts one to six arbitrary legs.",
            badge: "SCENARIO"
        ) {
            HStack(spacing: 10) {
                PlanningField("Spot", text: $optionSpot)
                PlanningField("DTE", text: $optionDays)
            }
            HStack(spacing: 10) {
                PlanningField("Long strike", text: $optionLongStrike)
                PlanningField("Long premium", text: $optionLongPremium)
            }
            HStack(spacing: 10) {
                PlanningField("Short strike", text: $optionShortStrike)
                PlanningField("Short premium", text: $optionShortPremium)
            }
            Button("Run option scenario") {
                Task {
                    await store.runOptionScenario(
                        spot: optionSpot, days: optionDays,
                        longStrike: optionLongStrike, longPremium: optionLongPremium,
                        shortStrike: optionShortStrike, shortPremium: optionShortPremium
                    )
                }
            }
            .buttonStyle(LabButtonStyle()).disabled(store.isLoading)
            if let result = store.optionScenarioResult {
                HStack(spacing: 10) {
                    LabMetricCard(label: "Sampled max profit", value: result.sampledMaxProfit.currency, detail: "Expiration grid")
                    LabMetricCard(label: "Sampled max loss", value: result.sampledMaxLoss.currency, detail: result.assignmentRisk ? "Assignment attention" : "Bounded sample")
                }
                Text("Delta \(result.modeledDeltaShares) · theta \(result.modeledThetaPerDay)/day · BE \(result.breakevens.joined(separator: " / "))")
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
        }
    }

    private var researchAndPortfolio: some View {
        LabSection(
            title: "Strategy and portfolio intelligence",
            subtitle: "Compare immutable versions, portfolio exposure, benchmark context, and data quality.",
            badge: "DESCRIPTIVE"
        ) {
            HStack {
                PlanningField("Comparison symbol", text: $comparisonSymbol, keyboard: .default)
                Button("Compare") { Task { await store.compareStrategies(comparisonSymbol) } }
                    .buttonStyle(.bordered)
            }
            ForEach(store.strategyComparisonResult?.comparisons.prefix(10) ?? []) { item in
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(item.name) · v\(item.versionNumber)").font(.caption.weight(.semibold))
                        Text(item.outOfSampleAvailable
                             ? "\(item.outOfSampleSessions) \(labLocalized("holdout sessions")) · \(labLocalized("full sample")) \(item.strategyReturnPercent ?? "—")%"
                             : labLocalized(item.outOfSampleReason ?? item.reason ?? "Holdout needs more data"))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 2) {
                        Text(item.outOfSampleReturnPercent.map { "\($0)% OOS" } ?? "—")
                            .font(.caption.monospacedDigit())
                        if item.versionID == store.strategyComparisonResult?.leaderVersionID {
                            Text("LEADER").font(.caption2.weight(.bold)).foregroundStyle(Color.labGreen)
                        }
                    }
                }
            }
            if let rule = store.strategyComparisonResult?.selectionRule {
                Text(labLocalized(rule)).font(.caption2).foregroundStyle(.secondary)
            }
            if let portfolio = store.portfolioIntelligence {
                Divider()
                HStack(spacing: 10) {
                    LabMetricCard(label: "Invested", value: "\(portfolio.investedPercent)%", detail: portfolio.cashEstimate.currency + " cash")
                    LabMetricCard(label: "Gross exposure", value: portfolio.grossExposure.currency, detail: portfolio.largestPositionPercent + "% largest")
                }
                Text(labLocalized(portfolio.scope)).font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private var researchCopilot: some View {
        LabSection(
            title: "Evidence-grounded research copilot",
            subtitle: "Local deterministic evidence composer with no external LLM API charge.",
            badge: "LOCAL"
        ) {
            PlanningField("Symbol", text: $copilotSymbol, keyboard: .default)
            PlanningField("Research question", text: $copilotQuestion, keyboard: .default)
            Button("Compose grounded brief") {
                Task { await store.composeResearchBrief(symbol: copilotSymbol, question: copilotQuestion) }
            }
            .buttonStyle(LabButtonStyle()).disabled(store.isLoading || copilotSymbol.isEmpty)
            if let result = store.researchCopilotResult {
                Text(result.answer).font(.subheadline)
                Text("THESIS").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                ForEach(result.thesis, id: \.self) { Text("• \($0)").font(.caption) }
                Text("COUNTER-THESIS").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                ForEach(result.counterThesis, id: \.self) { Text("• \($0)").font(.caption) }
            }
        }
    }

    private var reportsAndQuality: some View {
        LabSection(
            title: "Reports and data quality",
            subtitle: "Append-only daily and weekly records plus coverage and collection failures.",
            badge: "SYNCED"
        ) {
            HStack {
                Button("Generate daily report") { Task { await store.generateResearchReport("daily") } }
                    .buttonStyle(.borderedProminent).tint(Color.signalOrange)
                Button("Generate weekly report") { Task { await store.generateResearchReport("weekly") } }
                    .buttonStyle(.bordered)
            }
            if let quality = store.commandDataQuality {
                HStack(spacing: 10) {
                    LabMetricCard(label: "Symbols", value: "\(quality.summary.symbols)", detail: "\(quality.summary.dailyBars) bars")
                    LabMetricCard(label: "Stale", value: "\(quality.summary.staleSymbols)", detail: "\(quality.summary.recentFailedRuns) failed runs")
                }
                HStack(spacing: 10) {
                    LabMetricCard(label: "Missing intraday minutes", value: "\(quality.summary.intradayMissingMinutes ?? 0)", detail: "\(quality.summary.partialIntradaySessions ?? 0) partial sessions")
                    LabMetricCard(label: "Option quote warnings", value: "\((quality.summary.optionCrossedMarkets ?? 0) + (quality.summary.optionWideSpreads ?? 0))", detail: "Crossed and wide markets")
                }
            }
            ForEach(store.researchReports.prefix(10)) { report in
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(labLocalized(report.period)) · \(report.reportDate)").font(.caption.weight(.semibold))
                        Text(labLocalized(report.headline)).font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                }
            }
        }
    }

    private func loadControl() {
        guard let control = store.researchCommandCenter?.paperExecution else { return }
        paperEnabled = control.enabled
        paperMaximum = control.maxOrderNotional
        paperDailyStop = control.dailyLossLimit
        controlAcknowledged = false
    }
}

private struct PlanningField: View {
    let label: String
    @Binding var text: String
    let keyboard: UIKeyboardType
    let uppercase: Bool

    init(_ label: String, text: Binding<String>, keyboard: UIKeyboardType = .decimalPad, uppercase: Bool = false) {
        self.label = label
        _text = text
        self.keyboard = keyboard
        self.uppercase = uppercase
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(LocalizedStringKey(label))
                .textCase(.uppercase)
                .font(.system(size: 11, weight: .bold))
                .tracking(0.7)
                .foregroundStyle(.secondary)
            TextField(label, text: $text)
                .keyboardType(keyboard)
                .textInputAutocapitalization(uppercase ? .characters : .never)
                .autocorrectionDisabled()
                .padding(.horizontal, 11)
                .frame(minHeight: 42)
                .background(Color.white, in: RoundedRectangle(cornerRadius: 11))
                .overlay(RoundedRectangle(cornerRadius: 11).stroke(Color.labLine))
        }
    }
}

private struct AddWatchlistView: View {
    @ObservedObject var store: LabStore
    @Environment(\.dismiss) private var dismiss
    @State private var symbol = ""

    var body: some View {
        NavigationStack {
            Form {
                TextField("AAPL", text: $symbol)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
            }
            .navigationTitle("Add symbol")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") {
                        Task { if await store.addSymbol(symbol) { dismiss() } }
                    }
                    .disabled(symbol.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }
}

private struct PaperTradeView: View {
    @ObservedObject var store: LabStore
    @Environment(\.dismiss) private var dismiss
    @State private var symbol = ""
    @State private var assetType = "equity"
    @State private var side = "buy"
    @State private var quantity = "1"
    @State private var price = ""

    var body: some View {
        NavigationStack {
            Form {
                Picker("Asset", selection: $assetType) {
                    Text("Stock").tag("equity")
                    Text("Option").tag("option")
                }
                .pickerStyle(.segmented)
                TextField("Symbol", text: $symbol)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                Picker("Side", selection: $side) {
                    Text("Buy").tag("buy")
                    Text("Sell").tag("sell")
                }
                TextField("Quantity", text: $quantity).keyboardType(.decimalPad)
                TextField("Price", text: $price).keyboardType(.decimalPad)
            }
            .navigationTitle("Paper trade")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Record") {
                        Task {
                            if await store.recordTrade(
                                symbol: symbol,
                                assetType: assetType,
                                side: side,
                                quantity: quantity,
                                price: price
                            ) { dismiss() }
                        }
                    }
                    .disabled(symbol.isEmpty || quantity.isEmpty || price.isEmpty)
                }
            }
        }
    }
}

private struct AddPriceAlertView: View {
    @ObservedObject var store: LabStore
    @Environment(\.dismiss) private var dismiss
    @State private var symbol = ""
    @State private var direction = "above"
    @State private var threshold = ""

    var body: some View {
        NavigationStack {
            Form {
                TextField("Symbol", text: $symbol)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                Picker("Condition", selection: $direction) {
                    Text("At or above").tag("above")
                    Text("At or below").tag("below")
                }
                TextField("Threshold", text: $threshold).keyboardType(.decimalPad)
                Section {
                    Text("Uses cached end-of-day closes. It is not a live quote or trade instruction.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Price alert")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") {
                        Task {
                            if await store.addPriceAlert(
                                symbol: symbol, direction: direction, threshold: threshold
                            ) { dismiss() }
                        }
                    }
                    .disabled(symbol.isEmpty || threshold.isEmpty)
                }
            }
            .onAppear {
                if symbol.isEmpty { symbol = store.snapshot.watchlist.first?.symbol ?? "" }
            }
        }
    }
}

private struct JournalView: View {
    @ObservedObject var store: LabStore
    @State private var showingEntryForm = false
    @State private var selectedPlan: PlanReviewTarget?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    LabGuardrailCard(
                        eyebrow: "DECISION JOURNAL",
                        title: "The ledger remembers what confidence forgets.",
                        message: "Web and iPhone read the same append-only local history. Newest paper decisions appear first."
                    )
                    LabSection(title: "Recent activity", subtitle: "Stock and option events share one trail.", badge: "UTC STORED") {
                        if store.snapshot.recentTrades.isEmpty {
                            LabEmptyLine(text: "Your paper trades will appear here.")
                        } else {
                            ForEach(store.snapshot.recentTrades) { trade in
                                HStack(spacing: 11) {
                                    Text(labLocalized(trade.side.capitalized))
                                        .font(.system(size: 11, weight: .bold))
                                        .foregroundStyle(trade.side == "buy" ? Color.labGreen : Color.red)
                                        .frame(width: 32, alignment: .leading)
                                    VStack(alignment: .leading, spacing: 3) {
                                        HStack(spacing: 6) {
                                            Text(trade.symbol).font(.subheadline.weight(.semibold))
                                            Text(labLocalized(trade.assetType.capitalized)).font(.system(size: 11, weight: .bold)).foregroundStyle(.secondary)
                                        }
                                        Text(trade.executedAt.replacingOccurrences(of: "T", with: " ").prefix(16))
                                            .font(.caption.monospacedDigit())
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Text("\(trade.quantity) @ \(trade.price.currency)")
                                        .font(.caption.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                }
                                .padding(.vertical, 7)
                                if trade.id != store.snapshot.recentTrades.last?.id { Divider() }
                            }
                        }
                    }
                    LabSection(title: "Review dashboard", subtitle: "Self-recorded outcomes only.", badge: "NO INFERENCE") {
                        HStack(spacing: 10) {
                            LabMetricCard(
                                label: "Resolved",
                                value: "\(store.snapshot.reviewStats.resolvedReviews)",
                                detail: "\(store.snapshot.reviewStats.open) open"
                            )
                            LabMetricCard(
                                label: "Win rate",
                                value: store.snapshot.reviewStats.winRatePercent.map { "\($0)%" } ?? "—",
                                detail: "Decisive reviews"
                            )
                        }
                        LabMetricCard(
                            label: "Discipline",
                            value: store.snapshot.reviewStats.averageDisciplineScore.map { "\($0) / 5" } ?? "—",
                            detail: store.snapshot.reviewStats.scope
                        )
                    }
                    if let validation = store.validationDashboard {
                        LabSection(
                            title: "30–60 day validation campaign",
                            subtitle: "Frozen strategy context, multi-stock coverage, stored outcomes, paper reviews, and market-data evidence.",
                            badge: validation.readyForCapitalReview ? "READY TO REVIEW" : (validation.campaign.dayNumber > 0 ? "DAY \(validation.campaign.dayNumber) / \(validation.campaign.maximumDays)" : "NOT STARTED")
                        ) {
                            HStack(spacing: 10) {
                                LabMetricCard(label: "Observed", value: "\(validation.observationDays) days", detail: "Minimum \(validation.campaign.minimumDays) days")
                                LabMetricCard(label: "Decision samples", value: "\(validation.decisionValidation.decisive)", detail: validation.decisionValidation.targetFirstRatePercent.map { "\($0)% target first" } ?? "Collecting")
                            }
                            HStack(spacing: 10) {
                                LabMetricCard(label: "Paper reviews", value: "\(validation.paperReviews.resolved)", detail: validation.paperReviews.averageRMultiple.map { "\($0)R average" } ?? "Collecting")
                                LabMetricCard(label: "Intraday sessions", value: "\(validation.coverage.intradaySessions)", detail: "\(validation.coverage.optionChainSnapshots) option snapshots")
                            }
                            ForEach(validation.readinessGates) { gate in
                                HStack {
                                    Image(systemName: gate.passed ? "checkmark.circle.fill" : "clock")
                                        .foregroundStyle(gate.passed ? Color.labGreen : Color.signalOrange)
                                    Text(labLocalized(gate.label)).font(.caption)
                                    Spacer()
                                    Text("\(gate.value) / \(gate.required)").font(.caption.monospacedDigit())
                                }
                            }
                            if let operations = validation.operations {
                                HStack(spacing: 10) {
                                    LabMetricCard(
                                        label: "Daily decisions",
                                        value: operations.automation.dailyDecisions ? "Running" : "Blocked",
                                        detail: "Every \(operations.automation.refreshIntervalHours) hours"
                                    )
                                    LabMetricCard(
                                        label: "Intraday + options",
                                        value: operations.automation.intradayCollection && operations.automation.optionCollection ? "Running" : "Blocked",
                                        detail: "Missing data never counts"
                                    )
                                }
                                ForEach(operations.blockers + operations.warnings) { item in
                                    HStack(alignment: .top, spacing: 9) {
                                        Image(systemName: operations.blockers.contains(where: { $0.id == item.id }) ? "exclamationmark.octagon.fill" : "exclamationmark.triangle.fill")
                                            .foregroundStyle(operations.blockers.contains(where: { $0.id == item.id }) ? Color.red : Color.signalOrange)
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(labLocalized(item.label)).font(.caption.weight(.semibold))
                                            Text(labLocalized(item.detail)).font(.caption2).foregroundStyle(.secondary)
                                        }
                                    }
                                }
                                Button("Run validation cycle") {
                                    Task { await store.runValidationCycle() }
                                }
                                .buttonStyle(LabButtonStyle())
                                .disabled(store.isLoading)
                            }
                            Text(labLocalized(validation.campaign.instruction)).font(.caption2).foregroundStyle(.secondary)
                            Text(labLocalized(validation.scope)).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    LabSection(title: "Plan review loop", subtitle: "Compare saved worksheets with the choice you recorded.", badge: "SELF REPORTED") {
                        HStack(spacing: 10) {
                            LabMetricCard(
                                label: "Awaiting",
                                value: "\(store.snapshot.planReviewCenter.awaitingReview)",
                                detail: "No decision recorded"
                            )
                            LabMetricCard(
                                label: "Follow-through",
                                value: store.snapshot.planReviewCenter.followThroughPercent.map { "\($0)%" } ?? "—",
                                detail: "Process decisions"
                            )
                        }
                        LabMetricCard(
                            label: "Active followed",
                            value: "\(store.snapshot.planReviewCenter.activeFollowed)",
                            detail: "Open self-recorded outcomes"
                        )
                        if store.snapshot.planReviewCenter.optionAttention.isEmpty {
                            LabEmptyLine(text: "No open option worksheets need expiration attention.")
                        } else {
                            ForEach(store.snapshot.planReviewCenter.optionAttention) { item in
                                HStack(spacing: 10) {
                                    Image(systemName: item.daysRemaining <= 7 ? "calendar.badge.exclamationmark" : "calendar")
                                        .foregroundStyle(item.daysRemaining <= 7 ? Color.signalOrange : Color.labGreen)
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text("\(item.symbol) · \(item.expiration)")
                                            .font(.subheadline.weight(.semibold))
                                        Text(labLocalized(item.timingLabel))
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Button("Review") { selectedPlan = item.reviewTarget }
                                        .buttonStyle(.bordered)
                                }
                                .padding(.vertical, 5)
                            }
                        }
                        Text(labLocalized(store.snapshot.planReviewCenter.scope))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    LabSection(title: "Journal entries", subtitle: "Notes, reviews, and reusable lessons.", badge: "APPEND ONLY") {
                        if store.snapshot.journalEntries.isEmpty {
                            LabEmptyLine(text: "Your journal entries will appear here.")
                        } else {
                            ForEach(store.snapshot.journalEntries) { entry in
                                VStack(alignment: .leading, spacing: 7) {
                                    HStack {
                                        Text("\(entry.symbol) · \(labLocalized(entry.kind == "review" ? "Trade review" : entry.kind.capitalized))")
                                            .font(.subheadline.weight(.semibold))
                                        Spacer()
                                        Text(entry.outcome == "na" ? entry.setupTag : "\(labLocalized(entry.outcome.capitalized)) · \(entry.setupTag)")
                                            .font(.system(size: 11, weight: .bold))
                                            .foregroundStyle(.secondary)
                                    }
                                    Text(entry.title).font(.caption.weight(.semibold))
                                    Text(entry.body)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    if let score = entry.disciplineScore {
                                        Text("Discipline \(score) / 5")
                                            .font(.caption.monospacedDigit())
                                            .foregroundStyle(Color.labGreen)
                                    }
                                }
                                .padding(.vertical, 7)
                                if entry.id != store.snapshot.journalEntries.last?.id { Divider() }
                            }
                        }
                    }
                    LabSection(title: "Planning history", subtitle: "Risk and payoff worksheets share the same sync ledger.", badge: "RESEARCH ONLY") {
                        if store.snapshot.recentPlans.isEmpty {
                            LabEmptyLine(text: "Your saved planning worksheets will appear here.")
                        } else {
                            ForEach(store.snapshot.recentPlans) { plan in
                                HStack(spacing: 11) {
                                    Image(systemName: plan.kind == "day_trade" ? "scope" : "chart.xyaxis.line")
                                        .foregroundStyle(plan.kind == "day_trade" ? Color.signalOrange : Color.labGreen)
                                        .frame(width: 26)
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text("\(plan.symbol) · \(plan.kind == "day_trade" ? "DAY" : "OPTIONS")")
                                            .font(.subheadline.weight(.semibold))
                                        Text(plan.hypothesis)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(2)
                                    }
                                    Spacer()
                                    VStack(alignment: .trailing, spacing: 6) {
                                        Text(labLocalized(plan.kind == "day_trade" ? "\(plan.analysis.maximumWholeShares ?? 0) shares" : (plan.analysis.maxLoss ?? "0").currency))
                                            .font(.caption.monospacedDigit())
                                            .foregroundStyle(.secondary)
                                        Button("Review") { selectedPlan = plan.reviewTarget }
                                            .buttonStyle(.bordered)
                                    }
                                }
                                .padding(.vertical, 7)
                                if plan.id != store.snapshot.recentPlans.last?.id { Divider() }
                            }
                        }
                    }
                }
                .padding(16)
            }
            .background(Color.labPaper.ignoresSafeArea())
            .navigationTitle("Journal")
            .refreshable { await store.load() }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Add entry", systemImage: "square.and.pencil") {
                        showingEntryForm = true
                    }
                }
            }
            .sheet(isPresented: $showingEntryForm) {
                AddJournalEntryView(store: store)
            }
            .sheet(item: $selectedPlan) { plan in
                PlanReviewSheet(store: store, plan: plan)
            }
        }
    }
}

private struct PlanReviewSheet: View {
    @ObservedObject var store: LabStore
    let plan: PlanReviewTarget
    @Environment(\.dismiss) private var dismiss
    @State private var decision = "followed"
    @State private var outcome = "open"
    @State private var disciplineScore = ""
    @State private var note = ""
    @State private var actualEntry = ""
    @State private var actualExit = ""
    @State private var executionNote = ""
    @State private var screenshotItem: PhotosPickerItem?
    @State private var screenshotDataURL = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Worksheet") {
                    LabeledContent("Symbol", value: plan.symbol)
                    LabeledContent("Type", value: plan.kind == "day_trade" ? "Day trade" : "Options")
                    Text(plan.hypothesis).font(.caption).foregroundStyle(.secondary)
                }
                Section("Your decision") {
                    Picker("Decision", selection: $decision) {
                        Text("Followed").tag("followed")
                        Text("Skipped").tag("skipped")
                        Text("Invalidated").tag("invalidated")
                        Text("Expired").tag("expired")
                    }
                    if decision == "followed" {
                        Picker("Outcome", selection: $outcome) {
                            Text("Open").tag("open")
                            Text("Win").tag("win")
                            Text("Loss").tag("loss")
                            Text("Scratch").tag("scratch")
                        }
                    }
                    Picker("Discipline", selection: $disciplineScore) {
                        Text("Not scored").tag("")
                        ForEach(1...5, id: \.self) { score in Text("\(score) / 5").tag("\(score)") }
                    }
                    TextField("What happened?", text: $note, axis: .vertical)
                        .lineLimit(3...8)
                    if decision == "followed" {
                        TextField("Actual entry", text: $actualEntry).keyboardType(.decimalPad)
                        TextField("Actual exit", text: $actualExit).keyboardType(.decimalPad)
                        TextField("Execution deviation", text: $executionNote, axis: .vertical)
                            .lineLimit(2...6)
                        PhotosPicker(selection: $screenshotItem, matching: .images) {
                            Label(screenshotDataURL.isEmpty ? "Add chart screenshot" : "Screenshot ready", systemImage: "photo")
                        }
                        .onChange(of: screenshotItem) { _, item in
                            Task {
                                guard let data = try? await item?.loadTransferable(type: Data.self),
                                      let image = UIImage(data: data) else { return }
                                let maxSide: CGFloat = 1200
                                let scale = min(1, maxSide / max(image.size.width, image.size.height))
                                let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
                                let renderer = UIGraphicsImageRenderer(size: size)
                                let resized = renderer.image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
                                if let jpeg = resized.jpegData(compressionQuality: 0.62), jpeg.count <= 750_000 {
                                    screenshotDataURL = "data:image/jpeg;base64,\(jpeg.base64EncodedString())"
                                } else {
                                    store.errorMessage = "Chart screenshot must be 750 KB or smaller."
                                }
                            }
                        }
                    }
                }
                Section {
                    Text("This review records your own decision; it does not infer execution, advice, or brokerage status.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Plan review")
            .onChange(of: decision) { _, value in
                if value != "followed" { outcome = "na" }
                else if outcome == "na" { outcome = "open" }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            if await store.recordPlanReview(
                                planID: plan.id,
                                decision: decision,
                                outcome: outcome,
                                disciplineScore: disciplineScore,
                                note: note,
                                actualEntry: actualEntry,
                                actualExit: actualExit,
                                screenshotDataURL: screenshotDataURL,
                                executionNote: executionNote
                            ) { dismiss() }
                        }
                    }
                    .disabled(note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.isLoading)
                }
            }
        }
    }
}

private struct AddJournalEntryView: View {
    @ObservedObject var store: LabStore
    @Environment(\.dismiss) private var dismiss
    @State private var symbol = ""
    @State private var kind = "note"
    @State private var setupTag = "untagged"
    @State private var title = ""
    @State private var bodyText = ""
    @State private var outcome = "na"
    @State private var disciplineScore = ""

    var body: some View {
        NavigationStack {
            Form {
                TextField("Symbol", text: $symbol)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                Picker("Entry type", selection: $kind) {
                    Text("Note").tag("note")
                    Text("Trade review").tag("review")
                    Text("Lesson").tag("lesson")
                }
                TextField("Setup tag", text: $setupTag)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("Title", text: $title)
                TextField("What happened?", text: $bodyText, axis: .vertical)
                    .lineLimit(3...8)
                if kind == "review" {
                    Picker("Outcome", selection: $outcome) {
                        Text("Open").tag("open")
                        Text("Win").tag("win")
                        Text("Loss").tag("loss")
                        Text("Scratch").tag("scratch")
                    }
                    Picker("Discipline", selection: $disciplineScore) {
                        Text("Not scored").tag("")
                        ForEach(1...5, id: \.self) { score in Text("\(score) / 5").tag("\(score)") }
                    }
                }
            }
            .navigationTitle("Journal entry")
            .onAppear {
                if symbol.isEmpty { symbol = store.snapshot.watchlist.first?.symbol ?? "" }
            }
            .onChange(of: kind) { _, newKind in
                if newKind == "review", outcome == "na" { outcome = "open" }
                if newKind != "review" { outcome = "na"; disciplineScore = "" }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            if await store.recordJournalEntry(
                                symbol: symbol,
                                kind: kind,
                                setupTag: setupTag,
                                title: title,
                                body: bodyText,
                                outcome: outcome,
                                disciplineScore: disciplineScore
                            ) { dismiss() }
                        }
                    }
                    .disabled(symbol.isEmpty || title.isEmpty || bodyText.isEmpty || setupTag.isEmpty)
                }
            }
        }
    }
}

private struct PlanningDefaultField: View {
    let title: String
    let unit: String
    let detail: String
    @Binding var value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(LocalizedStringKey(title)).font(.subheadline.weight(.semibold))
            HStack {
                TextField(title, text: $value)
                    .keyboardType(.decimalPad)
                    .multilineTextAlignment(.trailing)
                Text(LocalizedStringKey(unit)).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            }
            Text(LocalizedStringKey(detail)).font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 3)
    }
}

private struct SettingsView: View {
    @ObservedObject var store: LabStore
    @AppStorage("appLanguage") private var appLanguage = "zh-Hans"
    @State private var marketAPIKey = ""
    @State private var alpacaKeyID = ""
    @State private var alpacaSecret = ""
    @State private var sourceTestStatus = "No connection test has run."
    @State private var strategyStyle = "balanced"
    @State private var timeHorizon = "swing"
    @State private var paperAccountSize = "25000"
    @State private var maxPositionPercent = "10"
    @State private var riskPerTradePercent = "0.5"
    @State private var minimumRewardRisk = "2"
    @State private var dailyLossLimit = "300"
    @State private var optionsDefinedRiskOnly = true
    @State private var decisionAutoRefresh = false
    @State private var decisionRefreshInterval = "24"
    @State private var exportURL: URL?
    @State private var showingShare = false
    @State private var showingImporter = false
    @State private var importPreview: PortfolioImportPreview?
    @State private var importFilename = ""
    @State private var importCSV = ""
    @State private var showingAccountDeletion = false
    @State private var showingAccountSecurity = false
    @State private var databaseBackupStatus = "Restore requires an explicit maintenance action."
    @State private var selectedBackup = ""
    @State private var restoreConfirmation = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Language") {
                    Picker("App language", selection: $appLanguage) {
                        Text("简体中文").tag("zh-Hans")
                        Text("English").tag("en")
                    }
                    Text("Language changes apply immediately on this iPhone. Account data and sync history are unchanged.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let user = store.currentUser {
                    Section("Private account") {
                        LabeledContent("Name", value: user.displayName)
                        LabeledContent("Email", value: user.email)
                        Button("Sign out", role: .destructive) { Task { await store.logout() } }
                        Button("Password and sessions") { showingAccountSecurity = true }
                    }
                }
                Section("Data source readiness") {
                    if let readiness = store.dataSourceReadiness {
                        LabeledContent("Overall", value: labLocalized(readiness.overall.replacingOccurrences(of: "_", with: " ").capitalized))
                        LabeledContent("Cached symbols", value: "\(readiness.coverage.cachedSymbols)")
                        LabeledContent("Decision-ready symbols", value: "\(readiness.coverage.decisionReadySymbols)")
                        LabeledContent("Paper order gate", value: labLocalized(readiness.paperOrdersEnabled ? "Enabled" : "Locked"))
                        ForEach(readiness.providers) { provider in
                            VStack(alignment: .leading, spacing: 5) {
                                HStack {
                                    Text(provider.label).font(.subheadline.weight(.semibold))
                                    Spacer()
                                    Text(labLocalized(provider.status.replacingOccurrences(of: "_", with: " ").uppercased()))
                                        .font(.caption.weight(.bold))
                                        .foregroundStyle(provider.configured ? Color.labGreen : Color.signalOrange)
                                }
                                Text(sourceDetail(provider, readiness: readiness))
                                    .font(.caption).foregroundStyle(.secondary)
                                Text(sourceCost(provider))
                                    .font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                        ForEach(Array(readiness.nextSteps.enumerated()), id: \.offset) { index, step in
                            Label("\(index + 1). \(labLocalized(step))", systemImage: "arrow.right.circle")
                                .font(.caption)
                        }
                        Text(labLocalized(readiness.scope)).font(.caption2).foregroundStyle(.secondary)
                    } else {
                        ProgressView("Checking data sources…")
                    }
                    Button("Refresh readiness") { Task { await store.refreshDataSourceReadiness() } }
                    Text(sourceTestStatus).font(.caption).foregroundStyle(.secondary)
                }
                Section("Risk & sizing defaults") {
                    Text("Choose Strategy style and Holding horizon from the Lab screen. The values below control dollar sizing and risk calculations.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    PlanningDefaultField(
                        title: "Paper account size",
                        unit: "USD",
                        detail: "Starting paper capital used for risk budgets, position caps, cash, and paper return.",
                        value: $paperAccountSize
                    )
                    PlanningDefaultField(
                        title: "Maximum position",
                        unit: "% / symbol",
                        detail: "Largest value in one symbol. Example: 10% of $25,000 equals $2,500.",
                        value: $maxPositionPercent
                    )
                    PlanningDefaultField(
                        title: "Risk per trade",
                        unit: "% / account",
                        detail: "Maximum planned loss to the stop. Example: 0.5% of $25,000 equals $125.",
                        value: $riskPerTradePercent
                    )
                    PlanningDefaultField(
                        title: "Minimum reward/risk",
                        unit: "ratio",
                        detail: "A value of 2 requires at least $2 potential reward for each $1 at risk.",
                        value: $minimumRewardRisk
                    )
                    PlanningDefaultField(
                        title: "Daily loss limit",
                        unit: "USD",
                        detail: "Day Trade stops adding risk capacity when today’s recorded loss reaches this amount.",
                        value: $dailyLossLimit
                    )
                    Toggle("Defined-risk options first", isOn: $optionsDefinedRiskOnly)
                    Text("Defaults the Options Lab to spreads with a calculable maximum loss.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("Save and sync defaults") {
                        Task {
                            _ = await store.updateInvestorProfile(
                                InvestorProfilePayload(
                                    strategyStyle: strategyStyle,
                                    timeHorizon: timeHorizon,
                                    paperAccountSize: paperAccountSize,
                                    maxPositionPercent: maxPositionPercent,
                                    riskPerTradePercent: riskPerTradePercent,
                                    minimumRewardRisk: minimumRewardRisk,
                                    dailyLossLimit: dailyLossLimit,
                                    optionsDefinedRiskOnly: optionsDefinedRiskOnly
                                )
                            )
                        }
                    }
                    .disabled(store.isLoading)
                    Text(labLocalized(store.snapshot.investorProfile.scope))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Research refresh and alerts") {
                    Toggle("Automatic research refresh", isOn: $decisionAutoRefresh)
                    TextField("Refresh interval (12–168 hours)", text: $decisionRefreshInterval)
                        .keyboardType(.numberPad)
                    Text("24 means the Mac refreshes watchlist prices, SEC research, earnings dates, and decisions about once per day while the local server is running.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("Save refresh schedule") {
                        Task {
                            _ = await store.updateDecisionSettings(
                                enabled: decisionAutoRefresh,
                                intervalHours: Int(decisionRefreshInterval) ?? 24
                            )
                        }
                    }
                    Button("Refresh all research now") {
                        Task { await store.refreshWatchlistDecisions() }
                    }
                    Button("Enable daily decision reminder") {
                        Task { await store.enableDecisionReminders() }
                    }
                    LabeledContent("Reminder", value: labLocalized(store.decisionReminderStatus))
                    Button("Enable SEC filing alerts") {
                        Task { await store.enableFilingAlerts() }
                    }
                    LabeledContent("SEC alerts", value: labLocalized(store.filingAlertStatus))
                    Text(labLocalized(store.snapshot.decisionCenter.settings.scope))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Connected devices") {
                    if store.snapshot.devices.isEmpty {
                        Text("No registered devices.").foregroundStyle(.secondary)
                    } else {
                        ForEach(store.snapshot.devices) { device in
                            HStack {
                                VStack(alignment: .leading, spacing: 4) {
                                    HStack {
                                        Text(device.name ?? "Device").font(.subheadline.weight(.semibold))
                                        if device.id == store.currentDeviceID {
                                            Text("THIS DEVICE")
                                                .font(.system(size: 9, weight: .bold))
                                                .foregroundStyle(Color.signalOrange)
                                        }
                                    }
                                    Text("\((device.platform ?? "unknown").uppercased()) · Revision \(device.lastRevision ?? 0)")
                                        .font(.caption.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                    Text(device.lastSeenAt ?? "Not synced")
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                if device.id != store.currentDeviceID {
                                    Button("Remove", role: .destructive) {
                                        Task { await store.deleteDevice(device.id) }
                                    }
                                }
                            }
                        }
                    }
                }
                Section("Import current positions") {
                    Button("Choose portfolio CSV") { showingImporter = true }
                    Text("Expected columns: symbol, quantity, average_cost, and optional asset_type. You preview every row before buy entries are appended to the paper ledger.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if store.snapshot.recentImports.isEmpty {
                        Text("No CSV portfolios imported yet.").foregroundStyle(.secondary)
                    } else {
                        ForEach(store.snapshot.recentImports.prefix(3)) { item in
                            LabeledContent(item.filename, value: "\(item.rowCount) positions")
                                .font(.caption)
                        }
                    }
                }
                Section("Option expiration reminders") {
                    Button("Enable local reminders") {
                        Task { await store.enableExpirationReminders() }
                    }
                    LabeledContent("Status", value: labLocalized(store.reminderStatus))
                    Text("Schedules local reminders seven days before and on the expiration date for open option worksheets. No notification service or subscription is used.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Data health") {
                    if let health = store.systemHealth {
                        LabeledContent("Database", value: labLocalized(health.database.integrity == "ok" ? "Passed" : health.database.integrity))
                        LabeledContent("Schema", value: "v\(health.schemaVersion)")
                        LabeledContent("Market cache", value: "\(labLocalized("\(health.marketCache.symbolCount) symbols")) · \(labLocalized("\(health.marketCache.barCount) bars"))")
                        LabeledContent("Records", value: "\(labLocalized("\(health.accountCounts.trades) trades")) · \(labLocalized("\(health.accountCounts.decisions) decisions"))")
                        LabeledContent("Latest backup", value: labLocalized(health.database.latestBackup?.filename ?? "None detected"))
                        LabeledContent("Backup retention", value: "\(health.database.backupCount) / \(health.database.backupRetention)")
                        ForEach(health.checks) { check in
                            LabeledContent(labLocalized(check.key.replacingOccurrences(of: "_", with: " ").capitalized), value: labLocalized(check.status.uppercased()))
                        }
                    } else {
                        Text("Health check has not run.").foregroundStyle(.secondary)
                    }
                    Button("Run and record health check") { Task { await store.runSystemHealthCheck() } }
                }
                Section("Portable backup") {
                    if store.currentUser?.role == "owner" {
                    Button("Create verified database backup") {
                        Task {
                            if let backup = await store.createDatabaseBackup() {
                                databaseBackupStatus = "\(backup.filename) · \(backup.integrity) · \(backup.restoreStatus)"
                            }
                        }
                    }
                    Text(databaseBackupStatus)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if !store.systemBackups.isEmpty {
                        Picker("Verified backup", selection: $selectedBackup) {
                            Text("Select a verified backup").tag("")
                            ForEach(store.systemBackups.filter(\.restorable)) { backup in
                                Text("\(backup.filename) · v\(backup.schemaVersion)").tag(backup.filename)
                            }
                        }
                        TextField(selectedBackup.isEmpty ? "RESTORE filename" : "RESTORE \(selectedBackup)", text: $restoreConfirmation)
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                        Button("Restore selected backup", role: .destructive) {
                            Task {
                                if await store.restoreDatabaseBackup(filename: selectedBackup, confirmation: restoreConfirmation) {
                                    databaseBackupStatus = "Restored \(selectedBackup); a safety backup was created first."
                                    restoreConfirmation = ""
                                    selectedBackup = ""
                                }
                            }
                        }
                        .disabled(selectedBackup.isEmpty || restoreConfirmation != "RESTORE \(selectedBackup)" || store.isLoading)
                    } else {
                        Text("Create a verified backup before using restore.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    } else {
                        Text("Database backup maintenance is available only to the workspace owner.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Button("Create account export") {
                        Task {
                            exportURL = await store.createAccountExport()
                            showingShare = exportURL != nil
                        }
                    }
                    .disabled(store.isLoading)
                    Text("Exports preferences, imports, watchlist, paper ledger, decisions, backtests, plans, journal, alerts, devices, and sync history. Passwords, sessions, and API keys are excluded.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Local API") {
                    TextField("Server URL", text: $store.serverURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button("Test and sync") { Task { await store.load() } }
                        .buttonStyle(LabButtonStyle())
                }
                Section("Market data") {
                    LabeledContent(
                        "Status",
                        value: store.marketStatus?.configured == true ? "Configured" : "API key required"
                    )
                    SecureField("Alpha Vantage API key", text: $marketAPIKey)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button(store.marketStatus?.configured == true ? "Replace saved key" : "Save to Mac Keychain") {
                        Task {
                            if await store.configureMarketData(marketAPIKey) {
                                marketAPIKey = ""
                            }
                        }
                    }
                    .disabled(marketAPIKey.isEmpty || store.currentUser?.role != "owner")
                    Button("Test Alpha Vantage connection") {
                        Task {
                            if let result = await store.testDataSource("alpha_vantage") {
                                sourceTestStatus = appLanguage == "zh-Hans"
                                    ? "连接测试通过：\(result.provider) · \(result.observations ?? 0) 根日线 · \(result.latestDataDate ?? "—")"
                                    : "Connection passed: \(result.provider) · \(result.observations ?? 0) bars · \(result.latestDataDate ?? "—")"
                            }
                        }
                    }
                    .disabled(store.marketStatus?.configured != true || store.isLoading || store.currentUser?.role != "owner")
                    Link(
                        "Get a free personal key",
                        destination: URL(string: "https://www.alphavantage.co/support/#api-key")!
                    )
                    Text("The key is sent to the Mac server and never returned to the iPhone.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Alpaca Paper and IEX") {
                    LabeledContent(
                        "Status",
                        value: store.marketStatus?.realtime.configured == true ? "Configured" : "API key required"
                    )
                    SecureField("Alpaca API key ID", text: $alpacaKeyID)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("Alpaca secret key", text: $alpacaSecret)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button(store.marketStatus?.realtime.configured == true ? "Replace saved credentials" : "Save to Mac Keychain") {
                        Task {
                            if await store.configureRealtime(keyID: alpacaKeyID, secret: alpacaSecret) {
                                alpacaKeyID = ""; alpacaSecret = ""
                            }
                        }
                    }
                    .disabled(alpacaKeyID.isEmpty || alpacaSecret.isEmpty || store.currentUser?.role != "owner")
                    Button("Test Alpaca Paper connection") {
                        Task {
                            if let result = await store.testDataSource("alpaca_paper") {
                                sourceTestStatus = appLanguage == "zh-Hans"
                                    ? "连接测试通过：\(result.provider) · \(result.accountStatus ?? "—") · \(result.tradingBlocked == true ? "已阻止交易" : "账户有效")"
                                    : "Connection passed: \(result.provider) · \(result.accountStatus ?? "—") · \(result.tradingBlocked == true ? "blocked" : "active")"
                            }
                        }
                    }
                    .disabled(store.marketStatus?.realtime.configured != true || store.isLoading || store.currentUser?.role != "owner")
                    Button("Synchronize Paper account") { Task { await store.synchronizePaperAccount() } }
                        .disabled(store.marketStatus?.realtime.configured != true || store.isLoading || store.currentUser?.role != "owner")
                    Text("Connection testing and account sync are read-only. Paper order routing remains a separate locked control in Command.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("Physical iPhone") {
                    Text("127.0.0.1 works in the Simulator. On a physical iPhone, use an HTTPS tunnel to the Mac running app.py so the session token is encrypted in transit.")
                }
                Section("Privacy & support") {
                    Link(
                        "Privacy policy",
                        destination: URL(string: "https://leocs777.github.io/stock-thesis-ledger/privacy/")!
                    )
                    Link(
                        "Open-source support",
                        destination: URL(string: "https://leocs777.github.io/stock-thesis-ledger/support/")!
                    )
                    Text("The open-source maintainer does not receive data from this reference build. Account and research data stay on the Server URL you choose.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Danger zone") {
                    Button("Delete account and all records", role: .destructive) {
                        showingAccountDeletion = true
                    }
                    Text("This permanently deletes the account from the local server and signs out every connected device.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .onAppear {
                loadProfile()
                Task { await store.loadSystemHealth() }
            }
            .onChange(of: store.snapshot.investorProfile.updatedAt) { _, _ in loadProfile() }
            .onChange(of: store.snapshot.decisionCenter.settings.updatedAt) { _, _ in loadProfile() }
            .fileImporter(
                isPresented: $showingImporter,
                allowedContentTypes: [.commaSeparatedText],
                allowsMultipleSelection: false
            ) { result in
                handleImportSelection(result)
            }
            .sheet(isPresented: $showingShare) {
                if let exportURL { ShareSheet(items: [exportURL]) }
            }
            .sheet(item: $importPreview) { preview in
                PortfolioImportPreviewView(
                    store: store,
                    preview: preview,
                    filename: importFilename,
                    csvText: importCSV
                )
            }
            .sheet(isPresented: $showingAccountDeletion) {
                DeleteAccountView(store: store)
            }
            .sheet(isPresented: $showingAccountSecurity) {
                AccountSecurityView(store: store)
            }
        }
    }

    private func sourceDetail(
        _ provider: DataSourceProvider, readiness: DataSourceReadiness
    ) -> String {
        guard appLanguage == "zh-Hans" else { return provider.detail }
        switch provider.key {
        case "alpha_vantage":
            return "已缓存 \(readiness.coverage.cachedSymbols) 只股票；最新日期 \(readiness.coverage.latestMarketDate ?? "无")。"
        case "alpaca_paper":
            return "模拟账户\(readiness.readyFor.paperAccount ? "已同步" : "尚未同步")；已有 \(readiness.coverage.optionSnapshots) 个期权快照。"
        default:
            return "公共只读访问，无需账户或 API 密钥。"
        }
    }

    private func sourceCost(_ provider: DataSourceProvider) -> String {
        guard appLanguage == "zh-Hans" else { return provider.cost }
        switch provider.key {
        case "alpha_vantage": return "个人免费密钥；应用默认缓存 12 小时。"
        case "alpaca_paper": return "使用 Alpaca Basic/模拟账户；Investor Lab 不收取路由费。"
        default: return "无需账户、API 密钥或 API 费用。"
        }
    }

    private func loadProfile() {
        let profile = store.snapshot.investorProfile
        strategyStyle = profile.strategyStyle
        timeHorizon = profile.timeHorizon
        paperAccountSize = profile.paperAccountSize
        maxPositionPercent = profile.maxPositionPercent
        riskPerTradePercent = profile.riskPerTradePercent
        minimumRewardRisk = profile.minimumRewardRisk
        dailyLossLimit = profile.dailyLossLimit
        optionsDefinedRiskOnly = profile.optionsDefinedRiskOnly
        decisionAutoRefresh = store.snapshot.decisionCenter.settings.autoRefreshEnabled
        decisionRefreshInterval = String(store.snapshot.decisionCenter.settings.refreshIntervalHours)
    }

    private func handleImportSelection(_ result: Result<[URL], Error>) {
        do {
            guard let url = try result.get().first else { return }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try Data(contentsOf: url, options: .mappedIfSafe)
            guard data.count <= 750_000 else { throw LabError("CSV file must be 750 KB or smaller.") }
            guard let text = String(data: data, encoding: .utf8) else {
                throw LabError("CSV file must use UTF-8 text encoding.")
            }
            importFilename = url.lastPathComponent
            importCSV = text
            Task {
                importPreview = await store.previewPortfolioImport(
                    filename: importFilename, csvText: importCSV
                )
            }
        } catch {
            store.errorMessage = error.localizedDescription
        }
    }
}

private struct PortfolioImportPreviewView: View {
    @ObservedObject var store: LabStore
    let preview: PortfolioImportPreview
    let filename: String
    let csvText: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    LabeledContent("Positions", value: "\(preview.rowCount)")
                    LabeledContent("Supplied cost basis", value: preview.totalCost.currency)
                    Text(preview.warning).font(.caption).foregroundStyle(.secondary)
                }
                Section("Rows") {
                    ForEach(preview.rows) { row in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(row.symbol).font(.headline)
                                Text(labLocalized(row.assetType.capitalized)).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            VStack(alignment: .trailing) {
                                Text(labLocalized("\(row.quantity) units"))
                                Text(row.averageCost.currency).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Import preview")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Import") {
                        Task {
                            if await store.importPortfolio(filename: filename, csvText: csvText) {
                                dismiss()
                            }
                        }
                    }
                    .disabled(store.isLoading)
                }
            }
        }
    }
}

private struct DeleteAccountView: View {
    @ObservedObject var store: LabStore
    @Environment(\.dismiss) private var dismiss
    @State private var password = ""
    @State private var confirmation = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    SecureField("Current password", text: $password)
                    TextField("Type DELETE", text: $confirmation)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                } footer: {
                    Text("Deletion cannot be undone. Export your account first if you need a portable copy.")
                }
            }
            .navigationTitle("Delete account")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Delete", role: .destructive) {
                        Task {
                            if await store.deleteAccount(password: password, confirmation: confirmation) {
                                dismiss()
                            }
                        }
                    }
                    .disabled(password.isEmpty || confirmation != "DELETE" || store.isLoading)
                }
            }
        }
    }
}

private struct AccountSecurityView: View {
    @ObservedObject var store: LabStore
    @Environment(\.dismiss) private var dismiss
    @State private var currentPassword = ""
    @State private var newPassword = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Change password") {
                    SecureField("Current password", text: $currentPassword)
                    SecureField("New password", text: $newPassword)
                    Button("Change password and sign out everywhere") {
                        Task {
                            if await store.changePassword(
                                currentPassword: currentPassword,
                                newPassword: newPassword
                            ) { dismiss() }
                        }
                    }
                    .disabled(currentPassword.isEmpty || newPassword.count < 12 || store.isLoading)
                }
                Section("Sessions") {
                    Button("Sign out on all devices", role: .destructive) {
                        Task {
                            if await store.logoutAll(currentPassword: currentPassword) { dismiss() }
                        }
                    }
                    .disabled(currentPassword.isEmpty || store.isLoading)
                    Text("Both actions revoke every Web and iPhone session and require a new sign-in.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Account security")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }
}

private struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

private enum AuthenticationState {
    case checking
    case signedOut
    case signedIn
}

@MainActor
private final class LabStore: ObservableObject {
    #if targetEnvironment(simulator)
    private static let defaultServerURL = "http://127.0.0.1:8000"
    #else
    private static let defaultServerURL = ""
    #endif

    @Published var authState = AuthenticationState.checking
    @Published var currentUser: UserProfile?
    @Published var snapshot = Snapshot.empty
    @Published var marketStatus: MarketStatus?
    @Published var dataSourceReadiness: DataSourceReadiness?
    @Published var marketResearch: MarketResearch?
    @Published var fundamentalResearch: FundamentalResearch?
    @Published var companySearchResults: [CompanySearchResult] = []
    @Published var decisionBundle: DecisionBundle?
    @Published var realtimeDayPlan: RealtimeDayPlan?
    @Published var dayTradeSessionReplay: DayTradeSessionReplay?
    @Published var dayTradeScanner: DayTradeScanner?
    @Published var optionChain: OptionChain?
    @Published var validationDashboard: ValidationDashboard?
    @Published var rebalanceResult: RebalanceResult?
    @Published var systemHealth: SystemHealth?
    @Published var systemBackups: [RestorableBackup] = []
    @Published var researchCommandCenter: ResearchCommandCenter?
    @Published var paperOrderLedger: PaperOrderLedger?
    @Published var universeScan: UniverseScan?
    @Published var notificationRuleCenter: NotificationRuleCenter?
    @Published var optionScenarioResult: OptionScenarioResult?
    @Published var strategyComparisonResult: StrategyComparisonResult?
    @Published var portfolioIntelligence: PortfolioIntelligence?
    @Published var commandDataQuality: CommandDataQuality?
    @Published var researchReports: [ResearchReport] = []
    @Published var researchCopilotResult: ResearchCopilotResult?
    @Published var reminderStatus = "Not enabled"
    @Published var decisionReminderStatus = "Not enabled"
    @Published var filingAlertStatus = "Not enabled"
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var serverURL: String {
        didSet { UserDefaults.standard.set(serverURL, forKey: "serverURL") }
    }

    private var accessToken: String?

    init() {
        let defaults = UserDefaults.standard
        let saved = defaults.string(forKey: "serverURL") ?? ""
        serverURL = saved
        if serverURL.isEmpty { serverURL = Self.defaultServerURL }
        defaults.set(serverURL, forKey: "serverURL")
    }

    var openPositionCount: Int {
        snapshot.portfolio.positions.filter { $0.quantity.decimal != 0 }.count
    }

    var currentDeviceID: String { deviceID() }

    func bootstrap() async {
        do {
            accessToken = try SecureTokenStore.read()
            guard accessToken != nil else {
                authState = .signedOut
                return
            }
            let session: SessionResponse = try await request(
                path: "/api/auth/session", method: "GET", body: Optional<WatchPayload>.none
            )
            currentUser = session.user
            authState = .signedIn
            await load()
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            do { try clearLocalSession() }
            catch { errorMessage = error.localizedDescription }
        }
    }

    func register(displayName: String, email: String, password: String) async -> Bool {
        await authenticate(
            path: "/api/auth/register",
            payload: AuthPayload(
                client: "ios", deviceID: currentDeviceID, deviceName: UIDevice.current.name,
                displayName: displayName, email: email, password: password
            )
        )
    }

    func login(email: String, password: String) async -> Bool {
        await authenticate(
            path: "/api/auth/login",
            payload: AuthPayload(
                client: "ios", deviceID: currentDeviceID, deviceName: UIDevice.current.name,
                displayName: nil, email: email, password: password
            )
        )
    }

    func logout() async {
        guard accessToken != nil else {
            do { try clearLocalSession() }
            catch { errorMessage = error.localizedDescription }
            return
        }
        do {
            let _: LogoutResponse = try await request(
                path: "/api/auth/logout", method: "POST", body: EmptyPayload()
            )
            try clearLocalSession()
        } catch let error as LabError where error.status == 401 {
            do { try clearLocalSession() }
            catch { errorMessage = error.localizedDescription }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func load() async {
        guard authState == .signedIn else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            try await registerDevice()
            try await syncSnapshot()
            try await loadMarketStatus()
            try await loadDataSourceReadiness()
            systemHealth = try await request(
                path: "/api/system/health", method: "GET", body: Optional<WatchPayload>.none
            )
            do {
                if currentUser?.role == "owner" {
                    systemBackups = try await request(
                        path: "/api/system/backups", method: "GET", body: Optional<WatchPayload>.none
                    )
                } else {
                    systemBackups = []
                }
            } catch let error as LabError where error.status == 404 {
                systemBackups = []
            }
            do {
                validationDashboard = try await request(
                    path: "/api/validation/dashboard?window_days=60", method: "GET",
                    body: Optional<WatchPayload>.none
                )
            } catch let error as LabError where error.status == 404 {
                validationDashboard = nil
            }
            await loadCommandCenter()
            if let symbol = snapshot.watchlist.first?.symbol { await loadMarket(symbol) }
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func addSymbol(_ symbol: String) async -> Bool {
        do {
            let _: WatchItem = try await request(
                path: "/api/watchlist", method: "POST", body: WatchPayload(symbol: symbol)
            )
            await load()
            return authState == .signedIn
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func recordTrade(
        symbol: String, assetType: String, side: String, quantity: String, price: String
    ) async -> Bool {
        do {
            let _: Trade = try await request(
                path: "/api/trades",
                method: "POST",
                body: TradePayload(
                    symbol: symbol,
                    assetType: assetType,
                    side: side,
                    quantity: quantity,
                    price: price
                )
            )
            await load()
            return authState == .signedIn
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func addPriceAlert(symbol: String, direction: String, threshold: String) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: PriceAlert = try await request(
                path: "/api/alerts",
                method: "POST",
                body: PriceAlertPayload(symbol: symbol, direction: direction, threshold: threshold)
            )
            try await syncSnapshot()
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func deletePriceAlert(_ id: String) async {
        do {
            let _: DeleteResponse = try await request(
                path: "/api/alerts/\(id)",
                method: "DELETE",
                body: Optional<EmptyPayload>.none
            )
            try await syncSnapshot()
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func recordJournalEntry(
        symbol: String,
        kind: String,
        setupTag: String,
        title: String,
        body: String,
        outcome: String,
        disciplineScore: String
    ) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: JournalEntry = try await request(
                path: "/api/journal",
                method: "POST",
                body: JournalEntryPayload(
                    symbol: symbol,
                    kind: kind,
                    setupTag: setupTag,
                    title: title,
                    body: body,
                    outcome: outcome,
                    disciplineScore: disciplineScore
                )
            )
            try await syncSnapshot()
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func recordPlanReview(
        planID: String,
        decision: String,
        outcome: String,
        disciplineScore: String,
        note: String,
        actualEntry: String,
        actualExit: String,
        screenshotDataURL: String,
        executionNote: String
    ) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: PlanReview = try await request(
                path: "/api/plans/\(planID)/reviews",
                method: "POST",
                body: PlanReviewPayload(
                    decision: decision,
                    outcome: outcome,
                    disciplineScore: disciplineScore,
                    note: note,
                    actualEntry: actualEntry,
                    actualExit: actualExit,
                    screenshotDataURL: screenshotDataURL,
                    executionNote: executionNote
                )
            )
            try await syncSnapshot()
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func recordDayPlan(
        symbol: String,
        direction: String,
        hypothesis: String,
        accountSize: String,
        entry: String,
        stop: String,
        target: String,
        riskPercent: String,
        maxPositionPercent: String,
        dailyLossLimit: String,
        currentDailyLoss: String,
        minimumRewardRisk: String,
        setupKey: String,
        live: RealtimeDayPlan?
    ) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: ResearchPlan = try await request(
                path: "/api/plans/day-trade",
                method: "POST",
                body: DayPlanPayload(
                    symbol: symbol,
                    direction: direction,
                    hypothesis: hypothesis,
                    accountSize: accountSize,
                    entry: entry,
                    stop: stop,
                    target: target,
                    riskPercent: riskPercent,
                    maxPositionPercent: maxPositionPercent,
                    dailyLossLimit: dailyLossLimit,
                    currentDailyLoss: currentDailyLoss,
                    minimumRewardRisk: minimumRewardRisk,
                    premarketHigh: live?.premarketHigh ?? "",
                    premarketLow: live?.premarketLow ?? "",
                    vwap: live?.vwap ?? "",
                    openingRangeHigh: live?.openingRangeHigh ?? "",
                    openingRangeLow: live?.openingRangeLow ?? "",
                    support: live?.support ?? "",
                    resistance: live?.resistance ?? "",
                    haltStatus: live == nil ? "unknown" : live?.halt.halted == true ? "halted" : "clear",
                    setupKey: setupKey
                )
            )
            try await syncSnapshot()
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func recordOptionPlan(
        symbol: String,
        strategy: String,
        hypothesis: String,
        expiration: Date,
        quantity: String,
        primaryStrike: String,
        primaryPremium: String,
        secondaryStrike: String,
        secondaryPremium: String,
        tertiaryStrike: String,
        tertiaryPremium: String,
        quaternaryStrike: String,
        quaternaryPremium: String
    ) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: ResearchPlan = try await request(
                path: "/api/plans/options",
                method: "POST",
                body: OptionPlanPayload(
                    symbol: symbol,
                    strategy: strategy,
                    hypothesis: hypothesis,
                    expiration: Self.planDateFormatter.string(from: expiration),
                    quantity: quantity,
                    primaryStrike: primaryStrike,
                    primaryPremium: primaryPremium,
                    secondaryStrike: secondaryStrike,
                    secondaryPremium: secondaryPremium,
                    tertiaryStrike: tertiaryStrike,
                    tertiaryPremium: tertiaryPremium,
                    quaternaryStrike: quaternaryStrike,
                    quaternaryPremium: quaternaryPremium
                )
            )
            try await syncSnapshot()
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    private static let planDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    func loadMarket(_ symbol: String) async {
        let trimmed = symbol.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        do {
            let encoded = trimmed.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? trimmed
            marketResearch = try await request(
                path: "/api/market/research/\(encoded)",
                method: "GET",
                body: Optional<WatchPayload>.none
            )
            fundamentalResearch = try await request(
                path: "/api/fundamentals/\(encoded)",
                method: "GET",
                body: Optional<WatchPayload>.none
            )
            decisionBundle = try await request(
                path: "/api/decisions/\(encoded)",
                method: "GET",
                body: Optional<WatchPayload>.none
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func refreshMarket(_ symbol: String) async {
        isLoading = true
        defer { isLoading = false }
        do {
            marketResearch = try await request(
                path: "/api/market/refresh",
                method: "POST",
                body: WatchPayload(symbol: symbol)
            )
            let encoded = symbol.trimmingCharacters(in: .whitespacesAndNewlines)
                .addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? symbol
            fundamentalResearch = try await request(
                path: "/api/fundamentals/\(encoded)",
                method: "GET",
                body: Optional<WatchPayload>.none
            )
            decisionBundle = try await request(
                path: "/api/decisions/\(encoded)",
                method: "GET",
                body: Optional<WatchPayload>.none
            )
            try await loadMarketStatus()
            try await syncSnapshot()
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func refreshFundamentals(_ symbol: String) async {
        let trimmed = symbol.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            fundamentalResearch = try await request(
                path: "/api/fundamentals/refresh",
                method: "POST",
                body: WatchPayload(symbol: trimmed)
            )
            try await syncSnapshot()
            let encoded = trimmed.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? trimmed
            decisionBundle = try await request(
                path: "/api/decisions/\(encoded)",
                method: "GET",
                body: Optional<WatchPayload>.none
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func searchCompanies(_ query: String) async {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        do {
            let encoded = trimmed.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? trimmed
            let response: CompanySearchResponse = try await request(
                path: "/api/search?q=\(encoded)", method: "GET", body: Optional<WatchPayload>.none
            )
            companySearchResults = response.results
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func refreshEarningsCalendar() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: EarningsCalendar = try await request(
                path: "/api/earnings-calendar/refresh", method: "POST", body: EmptyPayload()
            )
            try await syncSnapshot()
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func configureMarketData(_ apiKey: String) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: MarketConfiguration = try await request(
                path: "/api/market/configure",
                method: "POST",
                body: MarketConfigurationPayload(apiKey: apiKey)
            )
            try await loadMarketStatus()
            try await loadDataSourceReadiness()
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func configureRealtime(keyID: String, secret: String) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: MarketConfiguration = try await request(
                path: "/api/realtime/configure", method: "POST",
                body: RealtimeConfigurationPayload(apiKeyID: keyID, apiSecretKey: secret)
            )
            try await loadMarketStatus()
            try await loadDataSourceReadiness()
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func loadRealtimeDayPlan(_ symbol: String) async {
        let trimmed = symbol.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let encoded = trimmed.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? trimmed
            realtimeDayPlan = try await request(
                path: "/api/day-trade/live/\(encoded)", method: "GET",
                body: Optional<EmptyPayload>.none
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func loadDayTradeReplay(_ symbol: String, date: String? = nil) async {
        let trimmed = symbol.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        do {
            let encoded = trimmed.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? trimmed
            let query = date.map { "?date=\($0)" } ?? ""
            dayTradeSessionReplay = try await request(
                path: "/api/day-trade/replay/\(encoded)\(query)", method: "GET",
                body: Optional<EmptyPayload>.none
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func loadDayTradeScanner() async {
        isLoading = true
        defer { isLoading = false }
        do {
            dayTradeScanner = try await request(
                path: "/api/day-trade/scanner?limit=12", method: "GET",
                body: Optional<EmptyPayload>.none
            )
            if let scanner = dayTradeScanner { await notifyReadyDayTradeSetups(scanner) }
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    private func notifyReadyDayTradeSetups(_ scanner: DayTradeScanner) async {
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else { return }
        for candidate in scanner.alertCandidates.prefix(3) {
            let content = UNMutableNotificationContent()
            content.title = "Investor Lab paper setup"
            content.body = candidate.message
            content.sound = .default
            let identifier = "investorlab-day-\(candidate.key)"
            center.removePendingNotificationRequests(withIdentifiers: [identifier])
            do {
                try await center.add(UNNotificationRequest(identifier: identifier, content: content, trigger: nil))
            } catch {
                errorMessage = error.localizedDescription
                return
            }
        }
    }

    func loadOptionChain(
        _ symbol: String, right: String = "all", minimumDTE: String = "7",
        maximumDTE: String = "60", minimumVolume: String = "1",
        maximumSpread: String = "20"
    ) async {
        let trimmed = symbol.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let encoded = trimmed.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? trimmed
            let query = "right=\(right)&min_dte=\(minimumDTE)&max_dte=\(maximumDTE)&min_volume=\(minimumVolume)&max_spread_percent=\(maximumSpread)&liquid_only=true"
            optionChain = try await request(
                path: "/api/options/chain/\(encoded)?\(query)", method: "GET",
                body: Optional<EmptyPayload>.none
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func createDatabaseBackup() async -> DatabaseBackup? {
        isLoading = true
        defer { isLoading = false }
        do {
            let backup: DatabaseBackup = try await request(
                path: "/api/system/backup", method: "POST", body: EmptyPayload()
            )
            await loadSystemHealth()
            return backup
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return nil
        }
    }

    func restoreDatabaseBackup(filename: String, confirmation: String) async -> Bool {
        guard !filename.isEmpty else { return false }
        isLoading = true
        defer { isLoading = false }
        do {
            let _: DatabaseRestoreResult = try await request(
                path: "/api/system/restore", method: "POST",
                body: DatabaseRestorePayload(filename: filename, confirmation: confirmation)
            )
            try await syncSnapshot()
            await loadSystemHealth()
            try await loadDataSourceReadiness()
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func synchronizePaperAccount() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: PaperAccount = try await request(
                path: "/api/alpaca/paper-account/sync", method: "POST", body: EmptyPayload()
            )
            try await syncSnapshot()
            await loadSystemHealth()
            try await loadDataSourceReadiness()
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func loadCommandCenter() async {
        guard authState == .signedIn else { return }
        do {
            researchCommandCenter = try await request(
                path: "/api/research/command-center", method: "GET",
                body: Optional<EmptyPayload>.none
            )
            if currentUser?.role == "owner" {
                paperOrderLedger = try await request(
                    path: "/api/alpaca/paper-orders", method: "GET",
                    body: Optional<EmptyPayload>.none
                )
            } else {
                paperOrderLedger = nil
            }
            let notifications: NotificationRuleCenter = try await request(
                path: "/api/notifications/rules", method: "GET",
                body: Optional<EmptyPayload>.none
            )
            notificationRuleCenter = notifications
            await notifyOperationalAlertsIfAuthorized(notifications)
            portfolioIntelligence = try await request(
                path: "/api/portfolio/intelligence", method: "GET",
                body: Optional<EmptyPayload>.none
            )
            commandDataQuality = try await request(
                path: "/api/data-quality", method: "GET",
                body: Optional<EmptyPayload>.none
            )
            researchReports = try await request(
                path: "/api/reports", method: "GET",
                body: Optional<EmptyPayload>.none
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    private func notifyOperationalAlertsIfAuthorized(_ notifications: NotificationRuleCenter) async {
        guard let user = currentUser else { return }
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        guard settings.authorizationStatus == .authorized
                || settings.authorizationStatus == .provisional else { return }
        let defaults = UserDefaults.standard
        let key = "operationalAlertsSeen.\(user.id)"
        let seen = Set(defaults.stringArray(forKey: key) ?? [])
        let current = Set(notifications.operationalAlerts.map(\.id))
        do {
            for alert in notifications.operationalAlerts where !seen.contains(alert.id) {
                let content = UNMutableNotificationContent()
                content.title = labLocalized(alert.kind.replacingOccurrences(of: "_", with: " "))
                content.body = labLocalized(alert.detail)
                content.sound = .default
                try await center.add(
                    UNNotificationRequest(
                        identifier: "investorlab-operation-\(alert.id)",
                        content: content,
                        trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
                    )
                )
            }
            defaults.set(Array(current), forKey: key)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func updatePaperExecution(
        enabled: Bool, maximum: String, dailyStop: String, acknowledged: Bool
    ) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let control: PaperOrderControl = try await request(
                path: "/api/alpaca/paper-orders/control", method: "PATCH",
                body: PaperOrderControlPayload(
                    enabled: enabled, maxOrderNotional: maximum,
                    dailyLossLimit: dailyStop, acknowledged: acknowledged
                )
            )
            researchCommandCenter = researchCommandCenter?.replacing(control: control)
            paperOrderLedger = try await request(
                path: "/api/alpaca/paper-orders", method: "GET",
                body: Optional<EmptyPayload>.none
            )
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func submitPaperOrder(
        symbol: String, side: String, orderType: String, quantity: String,
        limitPrice: String, stopPrice: String, acknowledged: Bool
    ) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: RoutedPaperOrder = try await request(
                path: "/api/alpaca/paper-orders", method: "POST",
                body: PaperOrderPayload(
                    symbol: symbol, side: side, orderType: orderType,
                    timeInForce: "day", quantity: quantity,
                    limitPrice: limitPrice.isEmpty ? nil : limitPrice,
                    stopPrice: stopPrice.isEmpty ? nil : stopPrice,
                    clientOrderID: "ios-\(UUID().uuidString.lowercased())",
                    acknowledged: acknowledged
                )
            )
            paperOrderLedger = try await request(
                path: "/api/alpaca/paper-orders", method: "GET",
                body: Optional<EmptyPayload>.none
            )
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func cancelPaperOrder(_ order: RoutedPaperOrder) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: RoutedPaperOrder = try await request(
                path: "/api/alpaca/paper-orders/\(order.id)/cancel", method: "POST",
                body: PaperOrderConfirmationPayload(confirmation: "CANCEL PAPER \(order.symbol)")
            )
            paperOrderLedger = try await request(
                path: "/api/alpaca/paper-orders", method: "GET",
                body: Optional<EmptyPayload>.none
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func runUniverseScanner(name: String, symbols: String, minimumScore: String) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let symbolList = symbols.split(separator: ",").map {
                $0.trimmingCharacters(in: .whitespacesAndNewlines)
            }.filter { !$0.isEmpty }
            let preset: ScannerPreset = try await request(
                path: "/api/scanner-presets", method: "POST",
                body: ScannerPresetPayload(
                    name: name, symbols: symbolList,
                    filters: ScannerFiltersPayload(minimumScore: minimumScore)
                )
            )
            universeScan = try await request(
                path: "/api/scanner/run", method: "POST",
                body: UniverseScanPayload(presetID: preset.id)
            )
            researchCommandCenter = try await request(
                path: "/api/research/command-center", method: "GET",
                body: Optional<EmptyPayload>.none
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func addNotificationRule(kind: String, symbol: String, threshold: String) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: NotificationRule = try await request(
                path: "/api/notifications/rules", method: "POST",
                body: NotificationRulePayload(
                    kind: kind, symbol: symbol.isEmpty ? nil : symbol,
                    config: NotificationRuleConfigPayload(threshold: threshold, signal: "buy_candidate")
                )
            )
            notificationRuleCenter = try await request(
                path: "/api/notifications/rules", method: "GET",
                body: Optional<EmptyPayload>.none
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func runOptionScenario(
        spot: String, days: String, longStrike: String, longPremium: String,
        shortStrike: String, shortPremium: String
    ) async {
        isLoading = true
        defer { isLoading = false }
        do {
            optionScenarioResult = try await request(
                path: "/api/options/scenario", method: "POST",
                body: OptionScenarioPayload(
                    spot: spot, daysToExpiration: days, ivShiftPercent: "0",
                    legs: [
                        OptionScenarioLegPayload(right: "call", side: "buy", strike: longStrike, premium: longPremium, quantity: 1),
                        OptionScenarioLegPayload(right: "call", side: "sell", strike: shortStrike, premium: shortPremium, quantity: 1),
                    ]
                )
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func compareStrategies(_ symbol: String) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let trimmed = symbol.trimmingCharacters(in: .whitespacesAndNewlines)
            let encoded = trimmed.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? trimmed
            strategyComparisonResult = try await request(
                path: "/api/strategies/compare\(encoded.isEmpty ? "" : "?symbol=\(encoded)")",
                method: "GET", body: Optional<EmptyPayload>.none
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func composeResearchBrief(symbol: String, question: String) async {
        isLoading = true
        defer { isLoading = false }
        do {
            researchCopilotResult = try await request(
                path: "/api/research/copilot", method: "POST",
                body: ResearchCopilotPayload(symbol: symbol, question: question)
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func generateResearchReport(_ period: String) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: ResearchReport = try await request(
                path: "/api/reports", method: "POST", body: ResearchReportPayload(period: period)
            )
            researchReports = try await request(
                path: "/api/reports", method: "GET", body: Optional<EmptyPayload>.none
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func saveStrategyTemplate(
        name: String, technical: String, fundamental: String,
        valuation: String, portfolio: String, costBps: String
    ) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: StrategyTemplate = try await request(
                path: "/api/strategy-templates", method: "POST",
                body: StrategyTemplatePayload(
                    name: name, technicalWeight: technical, fundamentalWeight: fundamental,
                    valuationWeight: valuation, portfolioWeight: portfolio,
                    feeSlippageBps: costBps, activate: true
                )
            )
            try await syncSnapshot()
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func activateStrategyTemplate(_ id: String) async {
        do {
            let _: StrategyTemplate = try await request(
                path: "/api/strategy-templates/\(id)/activate", method: "POST", body: EmptyPayload()
            )
            try await syncSnapshot()
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func deleteStrategyTemplate(_ id: String) async {
        do {
            let _: DeleteResponse = try await request(
                path: "/api/strategy-templates/\(id)", method: "DELETE", body: Optional<EmptyPayload>.none
            )
            try await syncSnapshot()
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func calculateRebalance(_ text: String) async {
        let targets = text.split(separator: ",").map { part -> RebalanceTargetPayload in
            let pieces = part.split(separator: ":", maxSplits: 1).map(String.init)
            return RebalanceTargetPayload(
                symbol: pieces.first?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "",
                targetPercent: pieces.count > 1 ? pieces[1].trimmingCharacters(in: .whitespacesAndNewlines) : ""
            )
        }
        isLoading = true
        defer { isLoading = false }
        do {
            rebalanceResult = try await request(
                path: "/api/portfolio/rebalance", method: "POST",
                body: RebalancePayload(targets: targets)
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func updateInvestorProfile(_ payload: InvestorProfilePayload) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: InvestorProfile = try await request(
                path: "/api/investor-profile", method: "PATCH", body: payload
            )
            try await syncSnapshot()
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func generateDecision(_ symbol: String) async {
        let trimmed = symbol.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let _: DecisionRun = try await request(
                path: "/api/decisions", method: "POST", body: WatchPayload(symbol: trimmed)
            )
            try await syncSnapshot()
            let encoded = trimmed.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? trimmed
            decisionBundle = try await request(
                path: "/api/decisions/\(encoded)",
                method: "GET",
                body: Optional<WatchPayload>.none
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func refreshWatchlistDecisions() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: DecisionRefreshResult = try await request(
                path: "/api/decisions/refresh-watchlist", method: "POST", body: EmptyPayload()
            )
            try await syncSnapshot()
            try await loadMarketStatus()
            if let symbol = marketResearch?.symbol { await loadMarket(symbol) }
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func updateDecisionSettings(enabled: Bool, intervalHours: Int) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: DecisionSettings = try await request(
                path: "/api/decision-settings",
                method: "PATCH",
                body: DecisionSettingsPayload(
                    autoRefreshEnabled: enabled, refreshIntervalHours: intervalHours
                )
            )
            try await syncSnapshot()
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func createAccountExport() async -> URL? {
        isLoading = true
        defer { isLoading = false }
        do {
            let data = try await requestData(path: "/api/export", method: "GET", body: nil)
            let day = ISO8601DateFormatter().string(from: Date()).prefix(10)
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("investor-lab-\(day).json")
            try data.write(to: url, options: .atomic)
            return url
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return nil
        }
    }

    func previewPortfolioImport(filename: String, csvText: String) async -> PortfolioImportPreview? {
        isLoading = true
        defer { isLoading = false }
        do {
            return try await request(
                path: "/api/imports/portfolio/preview",
                method: "POST",
                body: PortfolioImportPayload(filename: filename, csvText: csvText)
            )
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return nil
        }
    }

    func importPortfolio(filename: String, csvText: String) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: PortfolioImportResult = try await request(
                path: "/api/imports/portfolio",
                method: "POST",
                body: PortfolioImportPayload(filename: filename, csvText: csvText)
            )
            try await syncSnapshot()
            systemHealth = try await request(
                path: "/api/system/health", method: "GET", body: Optional<WatchPayload>.none
            )
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func deleteDevice(_ id: String) async {
        do {
            let encoded = id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id
            let _: DeleteResponse = try await request(
                path: "/api/devices/\(encoded)",
                method: "DELETE",
                body: Optional<EmptyPayload>.none
            )
            try await syncSnapshot()
            await loadSystemHealth()
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func deleteAccount(password: String, confirmation: String) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: DeleteResponse = try await request(
                path: "/api/account/delete",
                method: "POST",
                body: DeleteAccountPayload(password: password, confirmation: confirmation)
            )
            try clearLocalSession()
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func changePassword(currentPassword: String, newPassword: String) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: AccountSecurityResponse = try await request(
                path: "/api/auth/change-password", method: "POST",
                body: ChangePasswordPayload(
                    currentPassword: currentPassword, newPassword: newPassword
                )
            )
            try clearLocalSession()
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func logoutAll(currentPassword: String) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let _: AccountSecurityResponse = try await request(
                path: "/api/auth/logout-all", method: "POST",
                body: LogoutAllPayload(currentPassword: currentPassword)
            )
            try clearLocalSession()
            return true
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return false
        }
    }

    func loadSystemHealth() async {
        guard authState == .signedIn else { return }
        do {
            systemHealth = try await request(
                path: "/api/system/health", method: "GET", body: Optional<WatchPayload>.none
            )
            if currentUser?.role == "owner" {
                systemBackups = try await request(
                    path: "/api/system/backups", method: "GET", body: Optional<WatchPayload>.none
                )
            } else {
                systemBackups = []
            }
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func runSystemHealthCheck() async {
        guard authState == .signedIn else { return }
        do {
            systemHealth = try await request(
                path: "/api/system/health-check", method: "POST", body: EmptyPayload()
            )
            if currentUser?.role == "owner" {
                systemBackups = try await request(
                    path: "/api/system/backups", method: "GET", body: Optional<WatchPayload>.none
                )
            } else {
                systemBackups = []
            }
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func runValidationCycle() async {
        guard authState == .signedIn else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let result: ValidationCycleResult = try await request(
                path: "/api/validation/run", method: "POST", body: EmptyPayload()
            )
            validationDashboard = result.dashboard
            systemHealth = try await request(
                path: "/api/system/health", method: "GET", body: Optional<EmptyPayload>.none
            )
            await loadCommandCenter()
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func enableExpirationReminders() async {
        do {
            let center = UNUserNotificationCenter.current()
            let granted = try await center.requestAuthorization(options: [.alert, .badge, .sound])
            guard granted else {
                reminderStatus = "Disabled in iPhone settings"
                return
            }
            try await scheduleOptionReminders()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func enableDecisionReminders() async {
        do {
            let center = UNUserNotificationCenter.current()
            let granted = try await center.requestAuthorization(options: [.alert, .badge, .sound])
            guard granted else {
                decisionReminderStatus = "Disabled in iPhone settings"
                return
            }
            UserDefaults.standard.set(true, forKey: "decisionRemindersEnabled")
            try await scheduleDecisionReminder()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func enableFilingAlerts() async {
        do {
            let center = UNUserNotificationCenter.current()
            let granted = try await center.requestAuthorization(options: [.alert, .badge, .sound])
            guard granted else {
                filingAlertStatus = "Disabled in iPhone settings"
                return
            }
            guard let user = currentUser else { return }
            let defaults = UserDefaults.standard
            let recentIDs = snapshot.secEvents.events.filter(\.isRecent).map(\.id)
            defaults.set(true, forKey: "filingAlertsEnabled")
            defaults.set(true, forKey: "filingAlertsBaseline.\(user.id)")
            defaults.set(recentIDs, forKey: "filingSeen.\(user.id)")
            filingAlertStatus = recentIDs.isEmpty
                ? "Enabled · no recent filings" : "Enabled · \(recentIDs.count) recent filings tracked"
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func authenticate(path: String, payload: AuthPayload) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            try clearLocalSession()
            let auth: AuthResponse = try await request(path: path, method: "POST", body: payload)
            guard let token = auth.accessToken, !token.isEmpty else {
                throw LabError("The server did not return an iOS session token.")
            }
            try SecureTokenStore.save(token)
            accessToken = token
            currentUser = auth.user
            authState = .signedIn
            try await registerDevice()
            try await syncSnapshot()
            systemHealth = try await request(
                path: "/api/system/health", method: "GET", body: Optional<WatchPayload>.none
            )
            if let notice = auth.securityNotice { errorMessage = labLocalized(notice) }
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    private func registerDevice() async throws {
        let _: DeviceRecord = try await request(
            path: "/api/devices",
            method: "POST",
            body: DevicePayload(deviceID: deviceID(), name: "iPhone", platform: "ios")
        )
    }

    private func loadMarketStatus() async throws {
        marketStatus = try await request(
            path: "/api/market/status", method: "GET", body: Optional<WatchPayload>.none
        )
    }

    private func loadDataSourceReadiness() async throws {
        dataSourceReadiness = try await request(
            path: "/api/data-sources/readiness", method: "GET",
            body: Optional<EmptyPayload>.none
        )
    }

    func refreshDataSourceReadiness() async {
        do { try await loadDataSourceReadiness() }
        catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
        }
    }

    func testDataSource(_ source: String, symbol: String = "SPY") async -> DataSourceTestResult? {
        isLoading = true
        defer { isLoading = false }
        do {
            let result: DataSourceTestResult = try await request(
                path: "/api/data-sources/test", method: "POST",
                body: DataSourceTestPayload(source: source, symbol: symbol)
            )
            try await loadDataSourceReadiness()
            return result
        } catch {
            if (error as? LabError)?.status != 401 { errorMessage = error.localizedDescription }
            return nil
        }
    }

    private func syncSnapshot() async throws {
        guard let user = currentUser else { throw LabError("Sign in before syncing.") }
        let defaults = UserDefaults.standard
        let key = "syncRevision.\(user.id)"
        var cursor = defaults.integer(forKey: key)
        var response: SyncResponse
        repeat {
            response = try await request(
                path: "/api/sync?since=\(cursor)&limit=500",
                method: "GET",
                body: Optional<WatchPayload>.none
            )
            cursor = response.cursor
            snapshot = response.snapshot
        } while response.hasMore
        defaults.set(cursor, forKey: key)
        let _: SyncAcknowledgement = try await request(
            path: "/api/sync/ack",
            method: "POST",
            body: SyncAcknowledgementPayload(deviceID: deviceID(), revision: cursor)
        )
        await refreshOptionRemindersIfAuthorized()
        await refreshDecisionNotificationsIfAuthorized()
        await refreshFilingNotificationsIfAuthorized()
    }

    private func refreshOptionRemindersIfAuthorized() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        guard settings.authorizationStatus == .authorized
                || settings.authorizationStatus == .provisional else {
            reminderStatus = settings.authorizationStatus == .denied
                ? "Disabled in iPhone settings" : "Not enabled"
            return
        }
        do { try await scheduleOptionReminders() }
        catch { reminderStatus = "Could not refresh reminders" }
    }

    private func scheduleOptionReminders() async throws {
        let center = UNUserNotificationCenter.current()
        let pending = await center.pendingNotificationRequests()
        center.removePendingNotificationRequests(
            withIdentifiers: pending.map(\.identifier).filter { $0.hasPrefix("investorlab-option-") }
        )
        var scheduled = 0
        let calendar = Calendar.current
        for item in snapshot.planReviewCenter.optionAttention.prefix(20) {
            let values = item.expiration.split(separator: "-").compactMap { Int($0) }
            guard values.count == 3 else { continue }
            var expiration = DateComponents()
            expiration.calendar = calendar
            expiration.timeZone = .current
            expiration.year = values[0]
            expiration.month = values[1]
            expiration.day = values[2]
            expiration.hour = 9
            guard let expirationDate = calendar.date(from: expiration) else { continue }
            for daysBefore in [7, 0] {
                guard let fireDate = calendar.date(
                    byAdding: .day, value: -daysBefore, to: expirationDate
                ), fireDate > Date() else { continue }
                let content = UNMutableNotificationContent()
                content.title = daysBefore == 0
                    ? "Option worksheet expires today" : "Option worksheet expires in 7 days"
                content.body = "\(item.symbol) · \(item.strategy.replacingOccurrences(of: "_", with: " ")). Review your saved paper plan."
                content.sound = .default
                let components = calendar.dateComponents(
                    [.year, .month, .day, .hour, .minute], from: fireDate
                )
                let request = UNNotificationRequest(
                    identifier: "investorlab-option-\(item.planID)-\(daysBefore)",
                    content: content,
                    trigger: UNCalendarNotificationTrigger(
                        dateMatching: components, repeats: false
                    )
                )
                try await center.add(request)
                scheduled += 1
            }
        }
        reminderStatus = scheduled == 0
            ? "Enabled · no upcoming expirations" : "\(scheduled) local reminders scheduled"
    }

    private func refreshDecisionNotificationsIfAuthorized() async {
        guard UserDefaults.standard.bool(forKey: "decisionRemindersEnabled") else {
            decisionReminderStatus = "Not enabled"
            return
        }
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        guard settings.authorizationStatus == .authorized
                || settings.authorizationStatus == .provisional else {
            decisionReminderStatus = settings.authorizationStatus == .denied
                ? "Disabled in iPhone settings" : "Not enabled"
            return
        }
        do {
            try await scheduleDecisionReminder()
            guard let user = currentUser else { return }
            for decision in snapshot.decisionCenter.latest {
                let key = "decisionSeen.\(user.id).\(decision.symbol)"
                let previous = UserDefaults.standard.string(forKey: key)
                UserDefaults.standard.set(decision.id, forKey: key)
                guard previous != nil, previous != decision.id, decision.change.signalChanged else { continue }
                let content = UNMutableNotificationContent()
                content.title = "\(decision.symbol): \(decision.signalLabel)"
                content.body = "\(decision.score.map { String($0) } ?? "—")/100 · \(decision.change.summary)"
                content.sound = .default
                try await center.add(
                    UNNotificationRequest(
                        identifier: "investorlab-decision-change-\(decision.id)",
                        content: content,
                        trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
                    )
                )
            }
        } catch {
            decisionReminderStatus = "Could not refresh reminders"
        }
    }

    private func scheduleDecisionReminder() async throws {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [
            "investorlab-decision-daily", "investorlab-plan-review-daily"
        ])
        let content = UNMutableNotificationContent()
        content.title = "Review today’s decisions"
        content.body = "Open Investor Lab to sync refreshed watchlist scores and signal changes."
        content.sound = .default
        var components = DateComponents()
        components.hour = 9
        components.minute = 15
        try await center.add(
            UNNotificationRequest(
                identifier: "investorlab-decision-daily",
                content: content,
                trigger: UNCalendarNotificationTrigger(dateMatching: components, repeats: true)
            )
        )
        let reviewCount = snapshot.planReviewCenter.awaitingReview
            + snapshot.planReviewCenter.activeFollowed
        if reviewCount > 0 {
            let reviewContent = UNMutableNotificationContent()
            reviewContent.title = labLocalized("Close the paper-plan loop")
            reviewContent.body = labLocalized(
                "\(reviewCount) saved plan(s) need a followed/skipped choice or outcome review."
            )
            reviewContent.sound = .default
            var reviewComponents = DateComponents()
            reviewComponents.hour = 16
            reviewComponents.minute = 30
            try await center.add(
                UNNotificationRequest(
                    identifier: "investorlab-plan-review-daily",
                    content: reviewContent,
                    trigger: UNCalendarNotificationTrigger(
                        dateMatching: reviewComponents, repeats: true
                    )
                )
            )
        }
        decisionReminderStatus = reviewCount > 0
            ? "Daily at 9:15 AM · review at 4:30 PM" : "Daily at 9:15 AM"
    }

    private func refreshFilingNotificationsIfAuthorized() async {
        guard UserDefaults.standard.bool(forKey: "filingAlertsEnabled") else {
            filingAlertStatus = "Not enabled"
            return
        }
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        guard settings.authorizationStatus == .authorized
                || settings.authorizationStatus == .provisional else {
            filingAlertStatus = settings.authorizationStatus == .denied
                ? "Disabled in iPhone settings" : "Not enabled"
            return
        }
        guard let user = currentUser else { return }
        let defaults = UserDefaults.standard
        let seenKey = "filingSeen.\(user.id)"
        let baselineKey = "filingAlertsBaseline.\(user.id)"
        let recent = snapshot.secEvents.events.filter(\.isRecent)
        if !defaults.bool(forKey: baselineKey) {
            defaults.set(recent.map(\.id), forKey: seenKey)
            defaults.set(true, forKey: baselineKey)
            filingAlertStatus = "Enabled · monitoring new filings"
            return
        }
        let seen = Set(defaults.stringArray(forKey: seenKey) ?? [])
        do {
            for event in recent where !seen.contains(event.id) {
                let content = UNMutableNotificationContent()
                content.title = "\(event.symbol): \(labLocalized(event.title))"
                content.body = "\(event.form) · \(event.filed)"
                content.sound = .default
                try await center.add(
                    UNNotificationRequest(
                        identifier: "investorlab-sec-\(event.id)",
                        content: content,
                        trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
                    )
                )
            }
            let remembered = Array(Set(seen).union(recent.map(\.id))).prefix(200)
            defaults.set(Array(remembered), forKey: seenKey)
            filingAlertStatus = "Enabled · monitoring new filings"
        } catch {
            filingAlertStatus = "Could not refresh filing alerts"
        }
    }

    private func deviceID() -> String {
        let key = "investorLabDeviceID"
        if let existing = UserDefaults.standard.string(forKey: key) { return existing }
        let created = "ios-\(UUID().uuidString.lowercased())"
        UserDefaults.standard.set(created, forKey: key)
        return created
    }

    private func clearLocalSession() throws {
        accessToken = nil
        currentUser = nil
        snapshot = .empty
        marketStatus = nil
        dataSourceReadiness = nil
        marketResearch = nil
        fundamentalResearch = nil
        decisionBundle = nil
        realtimeDayPlan = nil
        optionChain = nil
        systemHealth = nil
        researchCommandCenter = nil
        paperOrderLedger = nil
        universeScan = nil
        notificationRuleCenter = nil
        optionScenarioResult = nil
        strategyComparisonResult = nil
        portfolioIntelligence = nil
        commandDataQuality = nil
        researchReports = []
        researchCopilotResult = nil
        reminderStatus = "Not enabled"
        decisionReminderStatus = "Not enabled"
        filingAlertStatus = "Not enabled"
        UserDefaults.standard.set(false, forKey: "decisionRemindersEnabled")
        UserDefaults.standard.set(false, forKey: "filingAlertsEnabled")
        authState = .signedOut
        UNUserNotificationCenter.current().removeAllPendingNotificationRequests()
        try SecureTokenStore.delete()
    }

    private func request<Response: Decodable, Body: Encodable>(
        path: String, method: String, body: Body?
    ) async throws -> Response {
        let encodedBody = try body.map { try JSONEncoder().encode($0) }
        let data = try await requestData(path: path, method: method, body: encodedBody)
        return try JSONDecoder().decode(Response.self, from: data)
    }

    private func requestData(path: String, method: String, body: Data?) async throws -> Data {
        var rawServerURL = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        if !rawServerURL.contains("://") { rawServerURL = "https://\(rawServerURL)" }
        guard let components = URLComponents(string: rawServerURL),
              components.host != nil,
              ["http", "https"].contains(components.scheme?.lowercased() ?? ""),
              let base = components.url,
              let url = URL(string: path, relativeTo: base)?.absoluteURL else {
            throw LabError("Enter a valid server URL.")
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        if let token = accessToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = body
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw LabError("Invalid server response.") }
        guard 200..<300 ~= http.statusCode else {
            let message = (try? JSONDecoder().decode(ServerError.self, from: data).error)
                ?? "Server returned \(http.statusCode)."
            if http.statusCode == 401 { try clearLocalSession() }
            throw LabError(message, status: http.statusCode)
        }
        return data
    }
}

private struct UserProfile: Decodable {
    let id: String
    let email: String
    let displayName: String
    let role: String

    enum CodingKeys: String, CodingKey {
        case id, email, role
        case displayName = "display_name"
    }
}

private struct AuthResponse: Decodable {
    let user: UserProfile
    let accessToken: String?
    let securityNotice: String?

    enum CodingKeys: String, CodingKey {
        case user
        case accessToken = "access_token"
        case securityNotice = "security_notice"
    }
}

private struct SessionResponse: Decodable {
    let user: UserProfile
}

private struct SyncResponse: Decodable {
    let cursor: Int
    let hasMore: Bool
    let snapshot: Snapshot

    enum CodingKeys: String, CodingKey {
        case cursor, snapshot
        case hasMore = "has_more"
    }
}

private struct MarketStatus: Decodable {
    let provider: String
    let configured: Bool
    let freshness: String
    let cachedSymbols: Int
    let configurationSource: String
    let setupAvailable: Bool
    let realtime: RealtimeStatus

    enum CodingKeys: String, CodingKey {
        case provider, configured, freshness, realtime
        case cachedSymbols = "cached_symbols"
        case configurationSource = "configuration_source"
        case setupAvailable = "setup_available"
    }
}

private struct RealtimeStatus: Decodable {
    let provider: String
    let configured: Bool
    let configurationSource: String
    let feed: String
    let scope: String

    enum CodingKeys: String, CodingKey {
        case provider, configured, feed, scope
        case configurationSource = "configuration_source"
    }
}

private struct DataSourceReadiness: Decodable {
    let overall: String
    let providers: [DataSourceProvider]
    let readyFor: DataSourceCapabilities
    let coverage: DataSourceCoverage
    let paperOrdersEnabled: Bool
    let nextSteps: [String]
    let scope: String

    enum CodingKeys: String, CodingKey {
        case overall, providers, coverage, scope
        case readyFor = "ready_for"
        case paperOrdersEnabled = "paper_orders_enabled"
        case nextSteps = "next_steps"
    }
}

private struct DataSourceProvider: Decodable, Identifiable {
    var id: String { key }
    let key: String
    let label: String
    let configured: Bool
    let status: String
    let detail: String
    let cost: String
}

private struct DataSourceCapabilities: Decodable {
    let cachedResearch: Bool
    let currentEODRefresh: Bool
    let actionableDecisions: Bool
    let secResearch: Bool
    let realtimeDayTrade: Bool
    let liveOptionChain: Bool
    let paperAccount: Bool
    let paperOrders: Bool

    enum CodingKeys: String, CodingKey {
        case cachedResearch = "cached_research"
        case currentEODRefresh = "current_eod_refresh"
        case actionableDecisions = "actionable_decisions"
        case secResearch = "sec_research"
        case realtimeDayTrade = "realtime_day_trade"
        case liveOptionChain = "live_option_chain"
        case paperAccount = "paper_account"
        case paperOrders = "paper_orders"
    }
}

private struct DataSourceCoverage: Decodable {
    let cachedSymbols: Int
    let decisionReadySymbols: Int
    let latestMarketDate: String?
    let optionSnapshots: Int

    enum CodingKeys: String, CodingKey {
        case cachedSymbols = "cached_symbols"
        case decisionReadySymbols = "decision_ready_symbols"
        case latestMarketDate = "latest_market_date"
        case optionSnapshots = "option_snapshots"
    }
}

private struct DataSourceTestResult: Decodable {
    let provider: String
    let connected: Bool
    let observations: Int?
    let latestDataDate: String?
    let accountStatus: String?
    let tradingBlocked: Bool?

    enum CodingKeys: String, CodingKey {
        case provider, connected, observations
        case latestDataDate = "latest_data_date"
        case accountStatus = "account_status"
        case tradingBlocked = "trading_blocked"
    }
}

private struct SystemHealth: Decodable {
    let status: String
    let schemaVersion: Int
    let database: DatabaseHealth
    let accountCounts: AccountHealthCounts
    let marketCache: MarketCacheHealth
    let checks: [HealthCheck]

    enum CodingKeys: String, CodingKey {
        case status, database, checks
        case schemaVersion = "schema_version"
        case accountCounts = "account_counts"
        case marketCache = "market_cache"
    }
}

private struct DatabaseHealth: Decodable {
    let integrity: String
    let sizeBytes: Int
    let latestBackup: BackupHealth?
    let backupCount: Int
    let backupRetention: Int

    enum CodingKeys: String, CodingKey {
        case integrity
        case sizeBytes = "size_bytes"
        case latestBackup = "latest_backup"
        case backupCount = "backup_count"
        case backupRetention = "backup_retention"
    }
}

private struct HealthCheck: Decodable, Identifiable {
    let key: String
    let status: String
    let detail: String
    var id: String { key }
}

private struct BackupHealth: Decodable {
    let filename: String
    let sizeBytes: Int
    let modifiedAt: String

    enum CodingKeys: String, CodingKey {
        case filename
        case sizeBytes = "size_bytes"
        case modifiedAt = "modified_at"
    }
}

private struct DatabaseBackup: Decodable {
    let filename: String
    let sizeBytes: Int
    let createdAt: String
    let integrity: String
    let restoreStatus: String

    enum CodingKeys: String, CodingKey {
        case filename, integrity
        case sizeBytes = "size_bytes"
        case createdAt = "created_at"
        case restoreStatus = "restore_status"
    }
}

private struct RestorableBackup: Decodable, Identifiable {
    let filename: String
    let sizeBytes: Int
    let modifiedAt: String
    let integrity: String
    let schemaVersion: Int
    let restorable: Bool
    var id: String { filename }

    enum CodingKeys: String, CodingKey {
        case filename, integrity, restorable
        case sizeBytes = "size_bytes"
        case modifiedAt = "modified_at"
        case schemaVersion = "schema_version"
    }
}

private struct DatabaseRestoreResult: Decodable {
    let restored: Bool
    let filename: String
    let safetyBackup: String
    enum CodingKeys: String, CodingKey {
        case restored, filename
        case safetyBackup = "safety_backup"
    }
}

private struct PaperAccount: Decodable {
    let available: Bool
    let configured: Bool
    let readOnly: Bool
    let reason: String?
    let provider: String?
    let account: PaperAccountDetails?
    let positions: [PaperPosition]?
    let orders: [PaperOrder]?
    let fetchedAt: String?
    let scope: String?

    enum CodingKeys: String, CodingKey {
        case available, configured, reason, provider, account, positions, orders, scope
        case readOnly = "read_only"
        case fetchedAt = "fetched_at"
    }
}

private struct PaperAccountDetails: Decodable {
    let status: String?
    let cash: String?
    let equity: String?
    let buyingPower: String?
    let daytradeCount: String?
    let patternDayTrader: Bool?

    enum CodingKeys: String, CodingKey {
        case status, cash, equity
        case buyingPower = "buying_power"
        case daytradeCount = "daytrade_count"
        case patternDayTrader = "pattern_day_trader"
    }
}

private struct PaperPosition: Decodable, Identifiable {
    let symbol: String
    let qty: String?
    let side: String?
    let currentPrice: String?
    let marketValue: String?
    let unrealizedPL: String?
    var id: String { symbol }

    enum CodingKeys: String, CodingKey {
        case symbol, qty, side
        case currentPrice = "current_price"
        case marketValue = "market_value"
        case unrealizedPL = "unrealized_pl"
    }
}

private struct PaperOrder: Decodable, Identifiable {
    let id: String
    let symbol: String?
    let side: String?
    let type: String?
    let status: String?
    let qty: String?
}

private struct AccountHealthCounts: Decodable {
    let watchlist: Int
    let trades: Int
    let plans: Int
    let journalEntries: Int
    let alerts: Int
    let imports: Int
    let devices: Int
    let decisions: Int

    enum CodingKeys: String, CodingKey {
        case watchlist, trades, plans, alerts, imports, devices, decisions
        case journalEntries = "journal_entries"
    }
}

private struct MarketCacheHealth: Decodable {
    let symbolCount: Int
    let barCount: Int

    enum CodingKeys: String, CodingKey {
        case symbolCount = "symbol_count"
        case barCount = "bar_count"
    }
}

private struct RealtimeQuote: Decodable {
    let available: Bool
    let configured: Bool
    let latestPrice: String?
    let bid: String?
    let ask: String?
    let latestTradeAt: String?
    let sessionPhase: String?
    let reason: String?
    let scope: String?

    enum CodingKeys: String, CodingKey {
        case available, configured, bid, ask, reason, scope
        case latestPrice = "latest_price"
        case latestTradeAt = "latest_trade_at"
        case sessionPhase = "session_phase"
    }
}

private struct MarketResearch: Decodable, Identifiable {
    let available: Bool
    let symbol: String
    let reason: String?
    let tradingDate: String?
    let latestClose: String?
    let changePercent: String?
    let stateLabel: String?
    let explanation: String?
    let historicalScenario: MarketScenario?
    let bars: [MarketBar]?
    let rangeStats: MarketRangeStats?
    let dataQuality: MarketDataQuality?
    let realtimeQuote: RealtimeQuote?
    var id: String { symbol }

    enum CodingKeys: String, CodingKey {
        case available, symbol, reason, explanation, bars
        case tradingDate = "trading_date"
        case latestClose = "latest_close"
        case changePercent = "change_percent"
        case stateLabel = "state_label"
        case historicalScenario = "historical_scenario"
        case rangeStats = "range_stats"
        case dataQuality = "data_quality"
        case realtimeQuote = "realtime_quote"
    }
}

private struct MarketDataQuality: Decodable {
    let status: String
    let score: Int
    let decisionEligible: Bool
    let observations: Int?
    let latestAgeDays: Int?
    let priceAdjustment: String
    let blockers: [String]
    let warnings: [String]

    enum CodingKeys: String, CodingKey {
        case status, score, observations, blockers, warnings
        case decisionEligible = "decision_eligible"
        case latestAgeDays = "latest_age_days"
        case priceAdjustment = "price_adjustment"
    }
}

private struct FundamentalResearch: Decodable, Identifiable {
    let available: Bool
    let symbol: String
    let provider: String
    let reason: String?
    let cik: String?
    let companyName: String?
    let fiscalYear: Int?
    let periodEnd: String?
    let metrics: FundamentalMetrics?
    let annualHistory: [FundamentalPeriod]
    let quarterlyHistory: [FundamentalPeriod]?
    let quarterlyTrends: QuarterlyTrends?
    let companyProfile: CompanyProfile?
    let valuation: ValuationSnapshot?
    let filingComparison: FilingComparison?
    let filings: [SECFiling]
    let fetchedAt: String?
    let scope: String?
    let changes: FundamentalChanges?
    var id: String { symbol }

    enum CodingKeys: String, CodingKey {
        case available, symbol, provider, reason, cik, metrics, filings, scope, changes
        case companyName = "company_name"
        case fiscalYear = "fiscal_year"
        case periodEnd = "period_end"
        case annualHistory = "annual_history"
        case quarterlyHistory = "quarterly_history"
        case quarterlyTrends = "quarterly_trends"
        case companyProfile = "company_profile"
        case filingComparison = "filing_comparison"
        case valuation
        case fetchedAt = "fetched_at"
    }
}

private struct FundamentalChanges: Decodable {
    let detected: Bool
    let summary: String
    let newFilings: [SECFiling]

    enum CodingKeys: String, CodingKey {
        case detected, summary
        case newFilings = "new_filings"
    }
}

private struct FundamentalMetrics: Decodable {
    let revenue: Double?
    let netIncome: Double?
    let operatingCashFlow: Double?
    let capitalExpenditure: Double?
    let assets: Double?
    let liabilities: Double?
    let equity: Double?
    let dilutedEPS: Double?
    let dividendsPerShare: Double?
    let freeCashFlow: Double?
    let netMarginPercent: String?
    let liabilitiesToAssetsPercent: String?
    let revenueGrowthPercent: String?
    let netIncomeGrowthPercent: String?

    enum CodingKeys: String, CodingKey {
        case revenue, assets, liabilities, equity
        case netIncome = "net_income"
        case operatingCashFlow = "operating_cash_flow"
        case capitalExpenditure = "capital_expenditure"
        case dilutedEPS = "diluted_eps"
        case dividendsPerShare = "dividends_per_share"
        case freeCashFlow = "free_cash_flow"
        case netMarginPercent = "net_margin_percent"
        case liabilitiesToAssetsPercent = "liabilities_to_assets_percent"
        case revenueGrowthPercent = "revenue_growth_percent"
        case netIncomeGrowthPercent = "net_income_growth_percent"
    }
}

private struct FundamentalPeriod: Decodable, Identifiable {
    let fiscalYear: Int
    let fiscalPeriod: String?
    let periodEnd: String
    let revenue: Double?
    let netIncome: Double?
    let freeCashFlow: Double?
    var id: String { periodEnd }

    enum CodingKeys: String, CodingKey {
        case revenue
        case fiscalYear = "fiscal_year"
        case fiscalPeriod = "fiscal_period"
        case periodEnd = "period_end"
        case netIncome = "net_income"
        case freeCashFlow = "free_cash_flow"
    }
}

private struct CompanyProfile: Decodable {
    let industry: String?
    let sic: String?
    let exchange: String?
    let location: String?
}

private struct QuarterlyTrends: Decodable {
    let latestPeriodEnd: String?
    let revenueQoQPercent: String?
    let revenueYoYPercent: String?
    let netIncomeQoQPercent: String?
    let netIncomeYoYPercent: String?

    enum CodingKeys: String, CodingKey {
        case latestPeriodEnd = "latest_period_end"
        case revenueQoQPercent = "revenue_qoq_percent"
        case revenueYoYPercent = "revenue_yoy_percent"
        case netIncomeQoQPercent = "net_income_qoq_percent"
        case netIncomeYoYPercent = "net_income_yoy_percent"
    }
}

private struct ValuationSnapshot: Decodable {
    let available: Bool
    let reason: String?
    let price: String?
    let priceDate: String?
    let basis: String?
    let pe: String?
    let priceToSales: String?
    let priceToFCF: String?
    let dividendYieldPercent: String?
    let historicalPERange: ValuationRange?

    enum CodingKeys: String, CodingKey {
        case available, reason, price, basis, pe
        case priceDate = "price_date"
        case priceToSales = "price_to_sales"
        case priceToFCF = "price_to_fcf"
        case dividendYieldPercent = "dividend_yield_percent"
        case historicalPERange = "historical_pe_range"
    }
}

private struct ValuationRange: Decodable {
    let low: String
    let median: String
    let high: String
    let observations: Int
}

private struct FilingComparison: Decodable {
    let available: Bool
    let reason: String?
    let sections: FilingComparisonSections?
}

private struct FilingComparisonSections: Decodable {
    let riskFactors: FilingSectionChange?
    let managementDiscussion: FilingSectionChange?

    enum CodingKeys: String, CodingKey {
        case riskFactors = "risk_factors"
        case managementDiscussion = "management_discussion"
    }
}

private struct FilingSectionChange: Decodable {
    let available: Bool
    let similarityPercent: String
    let added: [String]
    let removed: [String]

    enum CodingKeys: String, CodingKey {
        case available, added, removed
        case similarityPercent = "similarity_percent"
    }
}

private struct CompanySearchResponse: Decodable { let results: [CompanySearchResult] }

private struct CompanySearchResult: Decodable, Identifiable {
    let symbol: String
    let name: String
    let cik: String
    let match: String
    var id: String { symbol }
}

private struct EarningsCalendar: Decodable {
    let available: Bool
    let events: [EarningsEvent]
    let reason: String?
    let scope: String?

    static let empty = EarningsCalendar(available: false, events: [], reason: nil, scope: nil)
}

private struct EarningsEvent: Decodable, Identifiable {
    let symbol: String
    let name: String
    let reportDate: String
    let fiscalDateEnding: String?
    let estimate: String?
    let currency: String?
    let daysUntil: Int
    var id: String { "\(symbol)-\(reportDate)" }

    enum CodingKeys: String, CodingKey {
        case symbol, name, estimate, currency
        case reportDate = "report_date"
        case fiscalDateEnding = "fiscal_date_ending"
        case daysUntil = "days_until"
    }
}

private struct SECFiling: Decodable, Identifiable {
    let form: String
    let filed: String
    let reportDate: String
    let url: String
    var id: String { url }

    enum CodingKeys: String, CodingKey {
        case form, filed, url
        case reportDate = "report_date"
    }
}

private struct SECEventCenter: Decodable {
    let events: [SECEvent]
    let recentCount: Int
    let attentionCount: Int
    let annualCount: Int
    let quarterlyCount: Int
    let scope: String

    enum CodingKeys: String, CodingKey {
        case events, scope
        case recentCount = "recent_count"
        case attentionCount = "attention_count"
        case annualCount = "annual_count"
        case quarterlyCount = "quarterly_count"
    }

    static let empty = SECEventCenter(
        events: [], recentCount: 0, attentionCount: 0, annualCount: 0,
        quarterlyCount: 0, scope: ""
    )
}

private struct SECEvent: Decodable, Identifiable {
    let id: String
    let symbol: String
    let form: String
    let filed: String
    let title: String
    let url: String
    let isRecent: Bool

    enum CodingKeys: String, CodingKey {
        case id, symbol, form, filed, title, url
        case isRecent = "is_recent"
    }
}

private struct MarketBar: Decodable, Identifiable {
    let tradingDate: String
    let open: String
    let high: String
    let low: String
    let close: String
    let volume: Int
    var id: String { tradingDate }
    var closeValue: Double { Double(close) ?? 0 }
    var volumeValue: Double { Double(volume) }

    enum CodingKeys: String, CodingKey {
        case open, high, low, close, volume
        case tradingDate = "trading_date"
    }
}

private struct MarketRangeStats: Decodable {
    let periodLabel: String
    let highClose: String
    let lowClose: String
    let periodReturnPercent: String
    let maxDrawdownPercent: String
    let annualizedVolatilityPercent: String
    let averageVolume: Int
    let latestVolume: Int
    let latestVolumeVsAveragePercent: String

    enum CodingKeys: String, CodingKey {
        case periodLabel = "period_label"
        case highClose = "high_close"
        case lowClose = "low_close"
        case periodReturnPercent = "period_return_percent"
        case maxDrawdownPercent = "max_drawdown_percent"
        case annualizedVolatilityPercent = "annualized_volatility_percent"
        case averageVolume = "average_volume"
        case latestVolume = "latest_volume"
        case latestVolumeVsAveragePercent = "latest_volume_vs_average_percent"
    }
}

private struct MarketScenario: Decodable {
    let strategyReturnPercent: String
    let buyHoldReturnPercent: String
    let maxDrawdownPercent: String

    enum CodingKeys: String, CodingKey {
        case strategyReturnPercent = "strategy_return_percent"
        case buyHoldReturnPercent = "buy_hold_return_percent"
        case maxDrawdownPercent = "max_drawdown_percent"
    }
}

private struct DecisionCenter: Decodable {
    let modelVersion: String
    let latest: [DecisionRun]
    let recentChanges: [DecisionRun]
    let settings: DecisionSettings

    enum CodingKeys: String, CodingKey {
        case latest, settings
        case modelVersion = "model_version"
        case recentChanges = "recent_changes"
    }

    static let empty = DecisionCenter(
        modelVersion: "decision-v4.1.1",
        latest: [],
        recentChanges: [],
        settings: .default
    )
}

private struct DecisionSettings: Decodable {
    let autoRefreshEnabled: Bool
    let refreshIntervalHours: Int
    let lastRefreshAt: String?
    let updatedAt: String
    let scope: String

    enum CodingKeys: String, CodingKey {
        case scope
        case autoRefreshEnabled = "auto_refresh_enabled"
        case refreshIntervalHours = "refresh_interval_hours"
        case lastRefreshAt = "last_refresh_at"
        case updatedAt = "updated_at"
    }

    static let `default` = DecisionSettings(
        autoRefreshEnabled: false,
        refreshIntervalHours: 24,
        lastRefreshAt: nil,
        updatedAt: "",
        scope: "Automatic refresh is off by default."
    )
}

private struct DecisionBundle: Decodable {
    let symbol: String
    let latest: DecisionRun?
    let history: [DecisionRun]
    let backtest: DecisionBacktest
    let validation: StrategyValidation?
}

private struct DecisionRun: Decodable, Identifiable {
    let id: String
    let symbol: String
    let modelVersion: String
    let signal: String
    let signalLabel: String
    let score: Int?
    let quality: String
    let qualityReason: String?
    let tradingDate: String?
    let latestClose: String?
    let validThrough: String?
    let hasPosition: Bool
    let position: DecisionPosition
    let factors: [DecisionFactor]
    let evidence: [String]
    let counterEvidence: [String]
    let observedRange: DecisionObservedRange
    let riskPlan: DecisionRiskPlan
    let pricePlan: DecisionPricePlan?
    let dataQuality: MarketDataQuality?
    let invalidation: String
    let backtest: DecisionBacktest
    let profile: DecisionProfile
    let strategy: DecisionStrategy?
    let change: DecisionChange
    let createdAt: String
    let disclaimer: String

    enum CodingKeys: String, CodingKey {
        case id, symbol, signal, score, quality, position, factors, evidence, invalidation
        case backtest, profile, strategy, change, disclaimer
        case dataQuality = "data_quality"
        case modelVersion = "model_version"
        case signalLabel = "signal_label"
        case qualityReason = "quality_reason"
        case tradingDate = "trading_date"
        case latestClose = "latest_close"
        case validThrough = "valid_through"
        case hasPosition = "has_position"
        case counterEvidence = "counter_evidence"
        case observedRange = "observed_range"
        case riskPlan = "risk_plan"
        case pricePlan = "price_plan"
        case createdAt = "created_at"
    }
}

private struct DecisionStrategy: Decodable {
    let label: String
    let technicalWeight: Int
    let fundamentalWeight: Int
    let valuationWeight: Int?
    let portfolioWeight: Int
    let fundamentalsPeriodEnd: String?
    let horizonNote: String
    let origin: String?
    let decisionRules: [String]?
    let dataSources: [String]?
    let pricePlanMethod: String?

    enum CodingKeys: String, CodingKey {
        case label, origin
        case technicalWeight = "technical_weight"
        case fundamentalWeight = "fundamental_weight"
        case valuationWeight = "valuation_weight"
        case portfolioWeight = "portfolio_weight"
        case fundamentalsPeriodEnd = "fundamentals_period_end"
        case horizonNote = "horizon_note"
        case decisionRules = "decision_rules"
        case dataSources = "data_sources"
        case pricePlanMethod = "price_plan_method"
    }
}

private struct DecisionFactor: Decodable, Identifiable {
    let key: String
    let label: String
    let score: Int
    let maxScore: Int
    let value: String
    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, label, score, value
        case maxScore = "max_score"
    }
}

private struct DecisionPosition: Decodable {
    let hasPosition: Bool
    let quantity: String
    let exposure: String
    let accountPercent: String
    let fitScore: Int
    let maximumPercent: String

    enum CodingKeys: String, CodingKey {
        case quantity, exposure
        case hasPosition = "has_position"
        case accountPercent = "account_percent"
        case fitScore = "fit_score"
        case maximumPercent = "maximum_percent"
    }
}

private struct DecisionObservedRange: Decodable {
    let low: String?
    let high: String?
    let label: String
}

private struct DecisionRiskPlan: Decodable {
    let paperAccountSize: String
    let riskBudget: String
    let maximumPositionValue: String
    let remainingPositionCapacity: String
    let note: String

    enum CodingKeys: String, CodingKey {
        case note
        case paperAccountSize = "paper_account_size"
        case riskBudget = "risk_budget"
        case maximumPositionValue = "maximum_position_value"
        case remainingPositionCapacity = "remaining_position_capacity"
    }
}

private struct DecisionPricePlan: Decodable {
    let available: Bool
    let reason: String?
    let method: String?
    let referencePrice: String?
    let buyZoneLow: String?
    let buyZoneHigh: String?
    let breakoutTrigger: String?
    let riskStop: String?
    let target1: String?
    let target2: String?
    let atr14: String?
    let riskPerShare: String?
    let minimumRewardRisk: String?
    let targetsActive: Bool?
    let action: String?
    let formula: [String]?
    let disclaimer: String?

    enum CodingKeys: String, CodingKey {
        case available, reason, method, action, formula, disclaimer
        case referencePrice = "reference_price"
        case buyZoneLow = "buy_zone_low"
        case buyZoneHigh = "buy_zone_high"
        case breakoutTrigger = "breakout_trigger"
        case riskStop = "risk_stop"
        case target1 = "target_1"
        case target2 = "target_2"
        case atr14 = "atr_14"
        case riskPerShare = "risk_per_share"
        case minimumRewardRisk = "minimum_reward_risk"
        case targetsActive = "targets_active"
    }
}

private struct DecisionProfile: Decodable {
    let strategyStyle: String
    let timeHorizon: String

    enum CodingKeys: String, CodingKey {
        case strategyStyle = "strategy_style"
        case timeHorizon = "time_horizon"
    }
}

private struct DecisionChange: Decodable {
    let signalChanged: Bool
    let previousSignal: String?
    let previousScore: Int?
    let scoreDelta: Int?
    let summary: String
    let factorChanges: [DecisionFactorChange]?
    let changedInputs: [String]?
    let explanation: String?

    enum CodingKeys: String, CodingKey {
        case summary, explanation
        case signalChanged = "signal_changed"
        case previousSignal = "previous_signal"
        case previousScore = "previous_score"
        case scoreDelta = "score_delta"
        case factorChanges = "factor_changes"
        case changedInputs = "changed_inputs"
    }
}

private struct DecisionFactorChange: Decodable, Identifiable {
    let key: String
    let label: String
    let previousScore: Int
    let currentScore: Int
    let scoreDelta: Int
    let direction: String
    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, label, direction
        case previousScore = "previous_score"
        case currentScore = "current_score"
        case scoreDelta = "score_delta"
    }
}

private struct DecisionBacktest: Decodable {
    let available: Bool
    let reason: String?
    let strategyReturnPercent: String?
    let buyHoldReturnPercent: String?
    let relativeReturnPercent: String?
    let maxDrawdownPercent: String?
    let completedTrades: Int?
    let winRatePercent: String?
    let exposurePercent: String?
    let rules: String?
    let assumption: String?
    let spyReturnPercent: String?
    let relativeToSPYPercent: String?
    let benchmarkAvailable: Bool?
    let equityCurve: [BacktestPoint]?
    let trades: [BacktestTrade]?
    let parameterSensitivity: [ParameterSensitivity]?
    let stability: BacktestStability?
    let outOfSample: BacktestHoldout?

    enum CodingKeys: String, CodingKey {
        case available, reason, rules, assumption
        case strategyReturnPercent = "strategy_return_percent"
        case buyHoldReturnPercent = "buy_hold_return_percent"
        case relativeReturnPercent = "relative_return_percent"
        case maxDrawdownPercent = "max_drawdown_percent"
        case completedTrades = "completed_trades"
        case winRatePercent = "win_rate_percent"
        case exposurePercent = "exposure_percent"
        case spyReturnPercent = "spy_return_percent"
        case relativeToSPYPercent = "relative_to_spy_percent"
        case benchmarkAvailable = "benchmark_available"
        case equityCurve = "equity_curve"
        case parameterSensitivity = "parameter_sensitivity"
        case stability
        case outOfSample = "out_of_sample"
        case trades
    }
}

private struct BacktestHoldout: Decodable {
    let available: Bool
    let method: String?
    let parametersFrozen: Bool?
    let sessions: Int?
    let sampleStart: String?
    let sampleEnd: String?
    let strategyReturnPercent: String?
    let relativeToSPYPercent: String?
    let reason: String?
    let remainingSessions: Int?

    enum CodingKeys: String, CodingKey {
        case available, method, sessions, reason
        case remainingSessions = "remaining_sessions"
        case parametersFrozen = "parameters_frozen"
        case sampleStart = "sample_start"
        case sampleEnd = "sample_end"
        case strategyReturnPercent = "strategy_return_percent"
        case relativeToSPYPercent = "relative_to_spy_percent"
    }
}

private struct ParameterSensitivity: Decodable, Identifiable {
    let label: String
    let entryThreshold: Int
    let exitThreshold: Int
    let strategyReturnPercent: String?
    let maxDrawdownPercent: String?
    let completedTrades: Int?
    var id: String { label }

    enum CodingKeys: String, CodingKey {
        case label
        case entryThreshold = "entry_threshold"
        case exitThreshold = "exit_threshold"
        case strategyReturnPercent = "strategy_return_percent"
        case maxDrawdownPercent = "max_drawdown_percent"
        case completedTrades = "completed_trades"
    }
}

private struct BacktestStability: Decodable {
    let label: String
    let returnRangePoints: String
    let note: String
    enum CodingKeys: String, CodingKey {
        case label, note
        case returnRangePoints = "return_range_points"
    }
}

private struct StrategyValidation: Decodable {
    let available: Bool
    let eligibleDecisions: Int
    let targetFirst: Int
    let stopFirst: Int
    let ambiguous: Int
    let unresolved: Int
    let targetFirstRatePercent: String?
    let averageMaximumAdverseExcursionPercent: String?
    let averageMaximumFavorableExcursionPercent: String?
    let outcomes: [StrategyOutcome]
    let parameterSensitivity: [ParameterSensitivity]
    let stability: BacktestStability?
    let reason: String?
    let scope: String

    enum CodingKeys: String, CodingKey {
        case available, ambiguous, unresolved, outcomes, stability, reason, scope
        case eligibleDecisions = "eligible_decisions"
        case targetFirst = "target_first"
        case stopFirst = "stop_first"
        case targetFirstRatePercent = "target_first_rate_percent"
        case averageMaximumAdverseExcursionPercent = "average_maximum_adverse_excursion_percent"
        case averageMaximumFavorableExcursionPercent = "average_maximum_favorable_excursion_percent"
        case parameterSensitivity = "parameter_sensitivity"
    }
}

private struct StrategyOutcome: Decodable, Identifiable {
    let decisionDate: String
    let signal: String
    let score: Int?
    let resolution: String
    let resolvedDate: String?
    let observedSessions: Int
    let maximumAdverseExcursionPercent: String
    let maximumFavorableExcursionPercent: String
    var id: String { "\(decisionDate)-\(signal)" }

    enum CodingKeys: String, CodingKey {
        case signal, score, resolution
        case decisionDate = "decision_date"
        case resolvedDate = "resolved_date"
        case observedSessions = "observed_sessions"
        case maximumAdverseExcursionPercent = "maximum_adverse_excursion_percent"
        case maximumFavorableExcursionPercent = "maximum_favorable_excursion_percent"
    }
}

private struct BacktestPoint: Decodable, Identifiable {
    let tradingDate: String
    let equity: String
    let score: Int
    let invested: Bool
    var id: String { tradingDate }
    var equityValue: Double { Double(equity) ?? 0 }

    enum CodingKeys: String, CodingKey {
        case equity, score, invested
        case tradingDate = "trading_date"
    }
}

private struct BacktestTrade: Decodable, Identifiable {
    let entryDate: String
    let entryPrice: String
    let exitDate: String
    let exitPrice: String
    let returnPercent: String
    let outcome: String
    let durationSessions: Int?
    let maximumAdverseExcursionPercent: String?
    let maximumFavorableExcursionPercent: String?
    var id: String { "\(entryDate)-\(exitDate)-\(entryPrice)" }

    enum CodingKeys: String, CodingKey {
        case outcome
        case entryDate = "entry_date"
        case entryPrice = "entry_price"
        case exitDate = "exit_date"
        case exitPrice = "exit_price"
        case returnPercent = "return_percent"
        case durationSessions = "duration_sessions"
        case maximumAdverseExcursionPercent = "maximum_adverse_excursion_percent"
        case maximumFavorableExcursionPercent = "maximum_favorable_excursion_percent"
    }
}

private struct DailyBriefing: Decodable {
    let headline: String
    let summary: String
    let attentionCount: Int
    let riskCount: Int
    let opportunityCount: Int
    let dataIssueCount: Int
    let tasks: [BriefingTask]
    let scope: String

    enum CodingKeys: String, CodingKey {
        case headline, summary, tasks, scope
        case attentionCount = "attention_count"
        case riskCount = "risk_count"
        case opportunityCount = "opportunity_count"
        case dataIssueCount = "data_issue_count"
    }

    static let empty = DailyBriefing(
        headline: "Daily action queue",
        summary: "No synchronized items yet.",
        attentionCount: 0,
        riskCount: 0,
        opportunityCount: 0,
        dataIssueCount: 0,
        tasks: [],
        scope: "Derived from cached end-of-day data."
    )
}

private struct BriefingTask: Decodable, Identifiable {
    let id: String
    let category: String
    let severity: String
    let symbol: String?
    let title: String
    let detail: String
    let destination: String
}

private struct WatchlistScreener: Decodable {
    let items: [ScreenerItem]
    let sort: String
    let freshness: String

    static let empty = WatchlistScreener(items: [], sort: "Risk actions first.", freshness: "end_of_day")
}

private struct ScreenerItem: Decodable, Identifiable {
    let symbol: String
    let segment: String
    let signal: String
    let signalLabel: String
    let score: Int?
    let hasPosition: Bool
    let accountPercent: String
    let latestClose: String?
    let changePercent: String?
    let stateLabel: String
    let tradingDate: String?
    let dataAgeDays: Int?
    let freshness: String
    var id: String { symbol }

    enum CodingKeys: String, CodingKey {
        case symbol, segment, signal, score, freshness
        case signalLabel = "signal_label"
        case hasPosition = "has_position"
        case accountPercent = "account_percent"
        case latestClose = "latest_close"
        case changePercent = "change_percent"
        case stateLabel = "state_label"
        case tradingDate = "trading_date"
        case dataAgeDays = "data_age_days"
    }
}

private struct PortfolioPerformance: Decodable {
    let estimatedCash: String
    let openCostBasis: String
    let marketValue: String
    let unrealizedPNL: String
    let realizedPNL: String
    let totalPNL: String
    let totalReturnPercent: String
    let estimatedAccountValue: String
    let positions: [PerformancePosition]
    let history: [PortfolioHistoryPoint]
    let disclaimer: String

    enum CodingKeys: String, CodingKey {
        case positions, history, disclaimer
        case estimatedCash = "estimated_cash"
        case openCostBasis = "open_cost_basis"
        case marketValue = "market_value"
        case unrealizedPNL = "unrealized_pnl"
        case realizedPNL = "realized_pnl"
        case totalPNL = "total_pnl"
        case totalReturnPercent = "total_return_percent"
        case estimatedAccountValue = "estimated_account_value"
    }

    static let empty = PortfolioPerformance(
        estimatedCash: "0",
        openCostBasis: "0",
        marketValue: "0",
        unrealizedPNL: "0",
        realizedPNL: "0",
        totalPNL: "0",
        totalReturnPercent: "0",
        estimatedAccountValue: "0",
        positions: [],
        history: [],
        disclaimer: "Paper performance uses cached daily closes."
    )
}

private struct PortfolioHistoryPoint: Decodable, Identifiable {
    let tradingDate: String
    let equity: String
    let cash: String
    let marketValue: String
    let realizedPNL: String
    let unrealizedPNL: String
    let totalPNL: String
    var id: String { tradingDate }

    enum CodingKeys: String, CodingKey {
        case equity, cash
        case tradingDate = "trading_date"
        case marketValue = "market_value"
        case realizedPNL = "realized_pnl"
        case unrealizedPNL = "unrealized_pnl"
        case totalPNL = "total_pnl"
    }
}

private struct PerformancePosition: Decodable, Identifiable {
    let symbol: String
    let assetType: String
    let quantity: String
    let referencePrice: String
    let marketValue: String
    let unrealizedPNL: String
    let unrealizedPercent: String
    let decisionLabel: String?
    var id: String { "\(symbol)-\(assetType)" }

    enum CodingKeys: String, CodingKey {
        case symbol, quantity
        case assetType = "asset_type"
        case referencePrice = "reference_price"
        case marketValue = "market_value"
        case unrealizedPNL = "unrealized_pnl"
        case unrealizedPercent = "unrealized_percent"
        case decisionLabel = "decision_label"
    }
}

private struct StrategyTemplate: Decodable, Identifiable {
    let id: String
    let name: String
    let technicalWeight: Int
    let fundamentalWeight: Int
    let valuationWeight: Int
    let portfolioWeight: Int
    let feeSlippageBps: Int
    let isActive: Bool
    let versionNumber: Int?
    let configHash: String?

    enum CodingKeys: String, CodingKey {
        case id, name
        case technicalWeight = "technical_weight"
        case fundamentalWeight = "fundamental_weight"
        case valuationWeight = "valuation_weight"
        case portfolioWeight = "portfolio_weight"
        case feeSlippageBps = "fee_slippage_bps"
        case isActive = "is_active"
        case versionNumber = "version_number"
        case configHash = "config_hash"
    }
}

private struct StrategyVersion: Decodable, Identifiable {
    let id: String
    let templateID: String?
    let name: String
    let versionNumber: Int
    let configHash: String
    let config: StrategyVersionConfig
    let createdAt: String
    let activatedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, name, config
        case templateID = "template_id"
        case versionNumber = "version_number"
        case configHash = "config_hash"
        case createdAt = "created_at"
        case activatedAt = "activated_at"
    }
}

private struct StrategyVersionConfig: Decodable {
    let technicalWeight: Int
    let fundamentalWeight: Int
    let valuationWeight: Int
    let portfolioWeight: Int
    let feeSlippageBps: Int

    enum CodingKeys: String, CodingKey {
        case technicalWeight = "technical_weight"
        case fundamentalWeight = "fundamental_weight"
        case valuationWeight = "valuation_weight"
        case portfolioWeight = "portfolio_weight"
        case feeSlippageBps = "fee_slippage_bps"
    }
}

private struct PortfolioActionCenter: Decodable {
    let actions: [PortfolioAction]
    let scope: String
    static let empty = PortfolioActionCenter(actions: [], scope: "")
}

private struct PortfolioAction: Decodable, Identifiable {
    let symbol: String
    let action: String
    let label: String
    let reason: String
    let score: Int?
    var id: String { "\(symbol)-\(action)" }
}

private struct DayTradeGuardrails: Decodable {
    let windowStart: String
    let windowEnd: String
    let estimatedDayTrades: Int
    let dayTradeRatioPercent: String
    let pdtThresholdReached: Bool
    let consecutiveLosses: Int
    let recordedLossToday: String
    let dailyLossLimit: String
    let stopConditions: [String]
    let stopTriggered: Bool
    let scope: String

    enum CodingKeys: String, CodingKey {
        case scope
        case windowStart = "window_start"
        case windowEnd = "window_end"
        case estimatedDayTrades = "estimated_day_trades"
        case dayTradeRatioPercent = "day_trade_ratio_percent"
        case pdtThresholdReached = "pdt_threshold_reached"
        case consecutiveLosses = "consecutive_losses"
        case recordedLossToday = "recorded_loss_today"
        case dailyLossLimit = "daily_loss_limit"
        case stopConditions = "stop_conditions"
        case stopTriggered = "stop_triggered"
    }

    static let empty = DayTradeGuardrails(
        windowStart: "", windowEnd: "", estimatedDayTrades: 0,
        dayTradeRatioPercent: "0", pdtThresholdReached: false,
        consecutiveLosses: 0, recordedLossToday: "0", dailyLossLimit: "0",
        stopConditions: [], stopTriggered: false, scope: ""
    )
}

private struct DayTradeScanner: Decodable {
    let generatedAt: String
    let marketClock: TradingMarketClock
    let rows: [DayTradeScannerRow]
    let alertCandidates: [DayTradeAlertCandidate]
    let errors: [DayTradeScannerError]
    let scope: String

    enum CodingKeys: String, CodingKey {
        case rows, errors, scope
        case generatedAt = "generated_at"
        case marketClock = "market_clock"
        case alertCandidates = "alert_candidates"
    }
}

private struct TradingMarketClock: Decodable {
    let isOpen: Bool
    let sessionPhase: String
    let source: String
    let nextOpen: String?
    let nextClose: String?

    enum CodingKeys: String, CodingKey {
        case source
        case isOpen = "is_open"
        case sessionPhase = "session_phase"
        case nextOpen = "next_open"
        case nextClose = "next_close"
    }
}

private struct DayTradeScannerRow: Decodable, Identifiable {
    let symbol: String
    let gapPercent: String?
    let relativeVolume: String?
    let spreadPercent: String?
    let bestSetup: DayTradeSetup?
    let readySetupCount: Int
    var id: String { symbol }

    enum CodingKeys: String, CodingKey {
        case symbol
        case gapPercent = "gap_percent"
        case relativeVolume = "relative_volume"
        case spreadPercent = "spread_percent"
        case bestSetup = "best_setup"
        case readySetupCount = "ready_setup_count"
    }
}

private struct DayTradeAlertCandidate: Decodable, Identifiable {
    let key: String
    let symbol: String
    let message: String
    var id: String { key }
}

private struct DayTradeScannerError: Decodable {
    let symbol: String
    let error: String
}

private struct RealtimeDayPlan: Decodable {
    let available: Bool
    let reason: String?
    let symbol: String
    let latestPrice: String?
    let spread: String?
    let premarketHigh: String?
    let premarketLow: String?
    let vwap: String?
    let openingRangeHigh: String?
    let openingRangeLow: String?
    let support: String?
    let resistance: String?
    let relativeVolume: String?
    let halt: RealtimeHalt
    let setups: [DayTradeSetup]?
    let replay: DayTradeReplay?
    let dataScope: String

    enum CodingKeys: String, CodingKey {
        case available, reason, symbol, spread, vwap, support, resistance, halt, setups, replay
        case latestPrice = "latest_price"
        case premarketHigh = "premarket_high"
        case premarketLow = "premarket_low"
        case openingRangeHigh = "opening_range_high"
        case openingRangeLow = "opening_range_low"
        case relativeVolume = "relative_volume"
        case dataScope = "data_scope"
    }
}

private struct DayTradeSetup: Decodable, Identifiable {
    let key: String
    let label: String
    let status: String
    let direction: String?
    let score: Int
    let entry: String?
    let stop: String?
    let target: String?
    let rewardRisk: String?
    let evidence: [String]
    let blockedReasons: [String]
    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, label, status, direction, score, entry, stop, target, evidence
        case rewardRisk = "reward_risk"
        case blockedReasons = "blocked_reasons"
    }
}

private struct DayTradeReplay: Decodable {
    let available: Bool
    let sessions: Int
    let triggeredSessions: Int
    let targetHits: Int
    let stopHits: Int
    let targetHitRatePercent: String?
    let averageRMultiple: String?
    let reason: String?
    let scope: String

    enum CodingKeys: String, CodingKey {
        case available, sessions, reason, scope
        case triggeredSessions = "triggered_sessions"
        case targetHits = "target_hits"
        case stopHits = "stop_hits"
        case targetHitRatePercent = "target_hit_rate_percent"
        case averageRMultiple = "average_r_multiple"
    }
}

private struct DayTradeSessionReplay: Decodable {
    let available: Bool
    let symbol: String
    let sessionDate: String?
    let availableDates: [String]
    let openingRangeHigh: String?
    let openingRangeLow: String?
    let direction: String?
    let entry: String?
    let stop: String?
    let target: String?
    let outcome: String?
    let realizedRMultiple: String?
    let triggerIndex: Int?
    let exitIndex: Int?
    let bars: [DayTradeReplayBar]?
    let reason: String?
    let scope: String?

    enum CodingKeys: String, CodingKey {
        case available, symbol, direction, entry, stop, target, outcome, bars, reason, scope
        case sessionDate = "session_date"
        case availableDates = "available_dates"
        case openingRangeHigh = "opening_range_high"
        case openingRangeLow = "opening_range_low"
        case realizedRMultiple = "realized_r_multiple"
        case triggerIndex = "trigger_index"
        case exitIndex = "exit_index"
    }
}

private struct DayTradeReplayBar: Decodable, Identifiable {
    let timestamp: String
    let open: String
    let high: String
    let low: String
    let close: String
    let volume: Int
    var id: String { timestamp }
}

private struct RealtimeHalt: Decodable {
    let halted: Bool
    let reasonCode: String?
    let source: String
    enum CodingKeys: String, CodingKey {
        case halted, source
        case reasonCode = "reason_code"
    }
}

private struct OptionChain: Decodable {
    let available: Bool
    let configured: Bool
    let symbol: String
    let reason: String?
    let feed: String
    let underlyingPrice: String?
    let summary: OptionChainSummary?
    let contracts: [OptionContract]?
    let candidates: [OptionCandidate]?
    let analytics: OptionAnalytics?
    let pagesLoaded: Int?
    let filteredContractCount: Int?
    let dataScope: String

    enum CodingKeys: String, CodingKey {
        case available, configured, symbol, reason, feed, summary, contracts, candidates, analytics
        case underlyingPrice = "underlying_price"
        case dataScope = "data_scope"
        case pagesLoaded = "pages_loaded"
        case filteredContractCount = "filtered_contract_count"
    }
}

private struct OptionAnalytics: Decodable {
    let termStructure: [OptionTermPoint]
    let portfolioGreeks: OptionPortfolioGreeks
    enum CodingKeys: String, CodingKey {
        case termStructure = "term_structure"
        case portfolioGreeks = "portfolio_greeks"
    }
}

private struct OptionTermPoint: Decodable {
    let expiration: String
    let daysToExpiration: Int
    let atmIVPercent: String
    enum CodingKeys: String, CodingKey {
        case expiration
        case daysToExpiration = "days_to_expiration"
        case atmIVPercent = "atm_iv_percent"
    }
}

private struct OptionPortfolioGreeks: Decodable {
    let matchedPositions: Int
    let deltaShares: String
    let gamma: String
    let thetaPerDay: String
    let vega: String
    enum CodingKeys: String, CodingKey {
        case gamma, vega
        case matchedPositions = "matched_positions"
        case deltaShares = "delta_shares"
        case thetaPerDay = "theta_per_day"
    }
}

private struct OptionChainSummary: Decodable {
    let contracts: Int
    let calls: Int
    let puts: Int
    let liquidContracts: Int
    let expirations: Int
    let atmIVPercent: String?
    let ivPercentile: String?
    let expectedMove: String?
    let expectedMoveDays: Int?

    enum CodingKeys: String, CodingKey {
        case contracts, calls, puts, expirations
        case liquidContracts = "liquid_contracts"
        case atmIVPercent = "atm_iv_percent"
        case ivPercentile = "iv_percentile"
        case expectedMove = "expected_move"
        case expectedMoveDays = "expected_move_days"
    }
}

private struct OptionContract: Decodable, Identifiable {
    let contractSymbol: String
    let expiration: String
    let daysToExpiration: Int
    let right: String
    let strike: String
    let bid: String
    let ask: String
    let spreadPercent: String?
    let volume: Int
    let openInterest: Int?
    let impliedVolatilityPercent: String?
    let delta: Double?
    let liquid: Bool
    var id: String { contractSymbol }

    enum CodingKeys: String, CodingKey {
        case expiration, right, strike, bid, ask, volume, delta, liquid
        case contractSymbol = "contract_symbol"
        case daysToExpiration = "days_to_expiration"
        case spreadPercent = "spread_percent"
        case openInterest = "open_interest"
        case impliedVolatilityPercent = "implied_volatility_percent"
    }
}

private struct OptionCandidate: Decodable, Identifiable {
    let strategy: String
    let label: String
    let expiration: String
    let daysToExpiration: Int
    let legs: [OptionCandidateLeg]
    let netDebitPerShare: String
    let maximumLossPerContract: String
    let maximumProfit: String
    let breakeven: String
    let liquidityNote: String
    var id: String { "\(strategy)-\(expiration)" }

    enum CodingKeys: String, CodingKey {
        case strategy, label, expiration, legs, breakeven
        case daysToExpiration = "days_to_expiration"
        case netDebitPerShare = "net_debit_per_share"
        case maximumLossPerContract = "maximum_loss_per_contract"
        case maximumProfit = "maximum_profit"
        case liquidityNote = "liquidity_note"
    }
}

private struct OptionCandidateLeg: Decodable {
    let action: String
    let contractSymbol: String
    let right: String
    let strike: String
    enum CodingKeys: String, CodingKey {
        case action, right, strike
        case contractSymbol = "contract_symbol"
    }
}

private struct ValidationDashboard: Decodable {
    let windowDays: Int
    let observationDays: Int
    let readyForCapitalReview: Bool
    let readinessGates: [ValidationGate]
    let decisionValidation: DecisionValidationSummary
    let paperReviews: PaperReviewValidation
    let coverage: ValidationCoverage
    let campaign: ValidationCampaign
    let operations: ValidationOperations?
    let scope: String

    enum CodingKeys: String, CodingKey {
        case scope, coverage, campaign, operations
        case windowDays = "window_days"
        case observationDays = "observation_days"
        case readyForCapitalReview = "ready_for_capital_review"
        case readinessGates = "readiness_gates"
        case decisionValidation = "decision_validation"
        case paperReviews = "paper_reviews"
    }
}

private struct ValidationOperations: Decodable {
    let status: String
    let pool: ValidationPool
    let automation: ValidationAutomation
    let blockers: [ValidationOperationItem]
    let warnings: [ValidationOperationItem]
    let instruction: String
}

private struct ValidationPool: Decodable {
    let symbols: [String]
    let count: Int
    let required: Int
}

private struct ValidationAutomation: Decodable {
    let schedulerRunning: Bool
    let dailyDecisions: Bool
    let refreshIntervalHours: Int
    let intradayCollection: Bool
    let optionCollection: Bool
    let verifiedDailyBackup: Bool
    let dailyWeeklyReports: Bool

    enum CodingKeys: String, CodingKey {
        case schedulerRunning = "scheduler_running"
        case dailyDecisions = "daily_decisions"
        case refreshIntervalHours = "refresh_interval_hours"
        case intradayCollection = "intraday_collection"
        case optionCollection = "option_collection"
        case verifiedDailyBackup = "verified_daily_backup"
        case dailyWeeklyReports = "daily_weekly_reports"
    }
}

private struct ValidationOperationItem: Decodable, Identifiable {
    let key: String
    let label: String
    let detail: String
    var id: String { key }
}

private struct ValidationCycleResult: Decodable {
    let status: String
    let blocked: [ValidationCycleBlocker]
    let dashboard: ValidationDashboard
}

private struct ValidationCycleBlocker: Decodable {
    let component: String
    let error: String
}

private struct ValidationCampaign: Decodable {
    let status: String
    let startedAt: String?
    let dayNumber: Int
    let minimumDays: Int
    let maximumDays: Int
    let parametersFrozen: Bool
    let strategyContexts: Int
    let instruction: String

    enum CodingKeys: String, CodingKey {
        case status, instruction
        case startedAt = "started_at"
        case dayNumber = "day_number"
        case minimumDays = "minimum_days"
        case maximumDays = "maximum_days"
        case parametersFrozen = "parameters_frozen"
        case strategyContexts = "strategy_contexts"
    }
}

private struct ValidationGate: Decodable, Identifiable {
    let key: String
    let label: String
    let passed: Bool
    let value: Int
    let required: Int
    var id: String { key }
}

private struct DecisionValidationSummary: Decodable {
    let eligible: Int
    let decisive: Int
    let targetFirst: Int
    let stopFirst: Int
    let targetFirstRatePercent: String?
    enum CodingKeys: String, CodingKey {
        case eligible, decisive
        case targetFirst = "target_first"
        case stopFirst = "stop_first"
        case targetFirstRatePercent = "target_first_rate_percent"
    }
}

private struct PaperReviewValidation: Decodable {
    let total: Int
    let followed: Int
    let resolved: Int
    let wins: Int
    let losses: Int
    let scratches: Int
    let averageRMultiple: String?
    let realizedPnL: String?
    enum CodingKeys: String, CodingKey {
        case total, followed, resolved, wins, losses, scratches
        case averageRMultiple = "average_r_multiple"
        case realizedPnL = "realized_pnl"
    }
}

private struct ValidationCoverage: Decodable {
    let intradaySessions: Int
    let optionChainSnapshots: Int
    let decisionSymbols: Int
    enum CodingKeys: String, CodingKey {
        case intradaySessions = "intraday_sessions"
        case optionChainSnapshots = "option_chain_snapshots"
        case decisionSymbols = "decision_symbols"
    }
}

private struct RebalanceResult: Decodable {
    let cashTargetPercent: String
    let rows: [RebalanceRow]
    let disclaimer: String
    enum CodingKeys: String, CodingKey {
        case rows, disclaimer
        case cashTargetPercent = "cash_target_percent"
    }
}

private struct RebalanceRow: Decodable, Identifiable {
    let symbol: String
    let targetPercent: String
    let referencePrice: String
    let currentValue: String
    let targetValue: String
    let shareAdjustment: String
    var id: String { symbol }
    enum CodingKeys: String, CodingKey {
        case symbol
        case targetPercent = "target_percent"
        case referencePrice = "reference_price"
        case currentValue = "current_value"
        case targetValue = "target_value"
        case shareAdjustment = "share_adjustment"
    }
}

private struct Snapshot: Decodable {
    let revision: Int
    let asOf: String
    let investorProfile: InvestorProfile
    let strategyTemplates: [StrategyTemplate]
    let strategyVersions: [StrategyVersion]?
    let paperAccount: PaperAccount?
    let devices: [DeviceRecord]
    let watchlist: [WatchItem]
    let watchlistResearch: [MarketResearch]
    let portfolio: Portfolio
    let portfolioRisk: PortfolioRisk
    let portfolioPerformance: PortfolioPerformance
    let portfolioActions: PortfolioActionCenter
    let recentTrades: [Trade]
    let recentImports: [PortfolioImportRecord]
    let recentPlans: [ResearchPlan]
    let planReviewCenter: PlanReviewCenter
    let dayTradeGuardrails: DayTradeGuardrails
    let journalEntries: [JournalEntry]
    let reviewStats: ReviewStats
    let alerts: AlertCenter
    let decisionCenter: DecisionCenter
    let watchlistScreener: WatchlistScreener
    let secEvents: SECEventCenter
    let dailyBriefing: DailyBriefing
    let earningsCalendar: EarningsCalendar

    enum CodingKeys: String, CodingKey {
        case asOf = "as_of"
        case revision, devices, watchlist, portfolio
        case investorProfile = "investor_profile"
        case strategyTemplates = "strategy_templates"
        case strategyVersions = "strategy_versions"
        case paperAccount = "paper_account"
        case watchlistResearch = "watchlist_research"
        case portfolioRisk = "portfolio_risk"
        case portfolioPerformance = "portfolio_performance"
        case portfolioActions = "portfolio_actions"
        case recentTrades = "recent_trades"
        case recentImports = "recent_imports"
        case recentPlans = "recent_plans"
        case planReviewCenter = "plan_review_center"
        case dayTradeGuardrails = "day_trade_guardrails"
        case journalEntries = "journal_entries"
        case reviewStats = "review_stats"
        case alerts
        case decisionCenter = "decision_center"
        case watchlistScreener = "watchlist_screener"
        case secEvents = "sec_events"
        case dailyBriefing = "daily_briefing"
        case earningsCalendar = "earnings_calendar"
    }

    static let empty = Snapshot(
        revision: 0,
        asOf: "",
        investorProfile: .default,
        strategyTemplates: [],
        strategyVersions: [],
        paperAccount: nil,
        devices: [],
        watchlist: [],
        watchlistResearch: [],
        portfolio: Portfolio(positions: [], realizedPNL: "0"),
        portfolioRisk: .empty,
        portfolioPerformance: .empty,
        portfolioActions: .empty,
        recentTrades: [],
        recentImports: [],
        recentPlans: [],
        planReviewCenter: .empty,
        dayTradeGuardrails: .empty,
        journalEntries: [],
        reviewStats: .empty,
        alerts: .empty,
        decisionCenter: .empty,
        watchlistScreener: .empty,
        secEvents: .empty,
        dailyBriefing: .empty,
        earningsCalendar: .empty
    )
}

private struct WatchItem: Decodable, Identifiable {
    let symbol: String
    let createdAt: String
    var id: String { symbol }

    enum CodingKeys: String, CodingKey {
        case symbol
        case createdAt = "created_at"
    }
}

private struct Portfolio: Decodable {
    let positions: [Position]
    let realizedPNL: String

    enum CodingKeys: String, CodingKey {
        case positions
        case realizedPNL = "realized_pnl"
    }
}

private struct PortfolioRisk: Decodable {
    let grossExposure: String
    let positionCount: Int
    let largestWeightPercent: String
    let concentrationLabel: String
    let positions: [RiskPosition]
    let sectors: [SectorExposure]
    let correlations: [PositionCorrelation]
    let stressScenarios: [StressScenario]
    let livePriceCount: Int
    let fallbackPriceCount: Int
    let disclaimer: String

    enum CodingKeys: String, CodingKey {
        case positions, sectors, correlations, disclaimer
        case stressScenarios = "stress_scenarios"
        case grossExposure = "gross_exposure"
        case positionCount = "position_count"
        case largestWeightPercent = "largest_weight_percent"
        case concentrationLabel = "concentration_label"
        case livePriceCount = "live_price_count"
        case fallbackPriceCount = "fallback_price_count"
    }

    static let empty = PortfolioRisk(
        grossExposure: "0",
        positionCount: 0,
        largestWeightPercent: "0",
        concentrationLabel: "No open positions",
        positions: [],
        sectors: [],
        correlations: [],
        stressScenarios: [],
        livePriceCount: 0,
        fallbackPriceCount: 0,
        disclaimer: "Descriptive exposure only."
    )
}

private struct RiskPosition: Decodable, Identifiable {
    let symbol: String
    let referenceSource: String
    let exposure: String
    let weightPercent: String
    let accountWeightPercent: String
    let sector: String
    let overMaxPosition: Bool
    var id: String { symbol }

    enum CodingKeys: String, CodingKey {
        case symbol, exposure, sector
        case referenceSource = "reference_source"
        case weightPercent = "weight_percent"
        case accountWeightPercent = "account_weight_percent"
        case overMaxPosition = "over_max_position"
    }
}

private struct SectorExposure: Decodable, Identifiable {
    let sector: String
    let exposure: String
    let weightPercent: String
    var id: String { sector }
    enum CodingKeys: String, CodingKey {
        case sector, exposure
        case weightPercent = "weight_percent"
    }
}

private struct PositionCorrelation: Decodable, Identifiable {
    let left: String
    let right: String
    let correlation: String
    let observations: Int
    var id: String { "\(left)-\(right)" }
}

private struct StressScenario: Decodable, Identifiable {
    let key: String
    let label: String
    let estimatedImpact: String
    let accountImpactPercent: String
    var id: String { key }
    enum CodingKeys: String, CodingKey {
        case key, label
        case estimatedImpact = "estimated_impact"
        case accountImpactPercent = "account_impact_percent"
    }
}

private struct AlertCenter: Decodable {
    let rules: [PriceAlert]
    let recentTriggers: [AlertTrigger]
    let freshness: String
    let disclaimer: String

    enum CodingKeys: String, CodingKey {
        case rules, freshness, disclaimer
        case recentTriggers = "recent_triggers"
    }

    static let empty = AlertCenter(
        rules: [],
        recentTriggers: [],
        freshness: "end_of_day",
        disclaimer: "Alerts use cached closes."
    )
}

private struct PriceAlert: Decodable, Identifiable {
    let id: String
    let symbol: String
    let direction: String
    let threshold: String
    let isTriggered: Bool
    let latestPrice: String?
    let tradingDate: String?

    enum CodingKeys: String, CodingKey {
        case id, symbol, direction, threshold
        case isTriggered = "is_triggered"
        case latestPrice = "latest_price"
        case tradingDate = "trading_date"
    }
}

private struct AlertTrigger: Decodable {
    let alertID: String
    let symbol: String
    let direction: String
    let threshold: String
    let observedPrice: String
    let tradingDate: String
    let source: String
    let triggeredAt: String

    enum CodingKeys: String, CodingKey {
        case symbol, direction, threshold, source
        case alertID = "alert_id"
        case observedPrice = "observed_price"
        case tradingDate = "trading_date"
        case triggeredAt = "triggered_at"
    }
}

private struct Position: Decodable, Identifiable {
    let symbol: String
    let assetType: String
    let quantity: String
    let averageCost: String
    let realizedPNL: String
    var id: String { symbol }

    enum CodingKeys: String, CodingKey {
        case symbol, quantity
        case assetType = "asset_type"
        case averageCost = "average_cost"
        case realizedPNL = "realized_pnl"
    }
}

private struct Trade: Decodable, Identifiable {
    let id: String
    let symbol: String
    let assetType: String
    let side: String
    let quantity: String
    let price: String
    let executedAt: String

    enum CodingKeys: String, CodingKey {
        case id, symbol, side, quantity, price
        case assetType = "asset_type"
        case executedAt = "executed_at"
    }
}

private struct PortfolioImportRecord: Decodable, Identifiable {
    let id: String
    let fingerprint: String
    let filename: String
    let rowCount: Int
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, fingerprint, filename
        case rowCount = "row_count"
        case createdAt = "created_at"
    }
}

private struct PortfolioImportPreview: Decodable, Identifiable {
    let filename: String
    let rowCount: Int
    let totalCost: String
    let rows: [PortfolioImportRow]
    let fingerprint: String
    let warning: String
    var id: String { fingerprint }

    enum CodingKeys: String, CodingKey {
        case filename, rows, fingerprint, warning
        case rowCount = "row_count"
        case totalCost = "total_cost"
    }
}

private struct PortfolioImportRow: Decodable, Identifiable {
    let symbol: String
    let quantity: String
    let averageCost: String
    let assetType: String
    var id: String { "\(symbol)-\(assetType)" }

    enum CodingKeys: String, CodingKey {
        case symbol, quantity
        case averageCost = "average_cost"
        case assetType = "asset_type"
    }
}

private struct ResearchPlan: Decodable, Identifiable {
    let id: String
    let kind: String
    let symbol: String
    let hypothesis: String
    let analysis: PlanAnalysis
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, kind, symbol, hypothesis, analysis
        case createdAt = "created_at"
    }
}

private struct PlanReviewTarget: Identifiable {
    let id: String
    let symbol: String
    let kind: String
    let hypothesis: String
}

private extension ResearchPlan {
    var reviewTarget: PlanReviewTarget {
        PlanReviewTarget(id: id, symbol: symbol, kind: kind, hypothesis: hypothesis)
    }
}

private struct PlanReviewCenter: Decodable {
    let totalPlans: Int
    let awaitingReview: Int
    let activeFollowed: Int
    let reviewedPlans: Int
    let followThroughPercent: String?
    let optionAttention: [OptionPlanAttention]
    let recentReviews: [PlanReview]
    let scope: String

    enum CodingKeys: String, CodingKey {
        case scope
        case totalPlans = "total_plans"
        case awaitingReview = "awaiting_review"
        case activeFollowed = "active_followed"
        case reviewedPlans = "reviewed_plans"
        case followThroughPercent = "follow_through_percent"
        case optionAttention = "option_attention"
        case recentReviews = "recent_reviews"
    }

    static let empty = PlanReviewCenter(
        totalPlans: 0,
        awaitingReview: 0,
        activeFollowed: 0,
        reviewedPlans: 0,
        followThroughPercent: nil,
        optionAttention: [],
        recentReviews: [],
        scope: "Self-recorded plan decisions."
    )
}

private struct OptionPlanAttention: Decodable, Identifiable {
    let planID: String
    let symbol: String
    let hypothesis: String
    let strategy: String
    let expiration: String
    let daysRemaining: Int
    let urgency: String
    var id: String { planID }

    enum CodingKeys: String, CodingKey {
        case symbol, hypothesis, strategy, expiration, urgency
        case planID = "plan_id"
        case daysRemaining = "days_remaining"
    }

    var timingLabel: String {
        if daysRemaining < 0 { return "\(-daysRemaining) days past expiration" }
        if daysRemaining == 0 { return "Expires today" }
        return "\(daysRemaining) days remaining"
    }


    var reviewTarget: PlanReviewTarget {
        PlanReviewTarget(id: planID, symbol: symbol, kind: "options", hypothesis: hypothesis)
    }
}

private struct PlanReview: Decodable, Identifiable {
    let id: String
    let planID: String
    let kind: String
    let symbol: String
    let decision: String
    let outcome: String
    let disciplineScore: Int?
    let note: String
    let actualEntry: String?
    let actualExit: String?
    let screenshotDataURL: String?
    let executionNote: String
    let realizedPNL: String?
    let realizedRMultiple: String?
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, kind, symbol, decision, outcome, note
        case planID = "plan_id"
        case disciplineScore = "discipline_score"
        case actualEntry = "actual_entry"
        case actualExit = "actual_exit"
        case screenshotDataURL = "screenshot_data_url"
        case executionNote = "execution_note"
        case realizedPNL = "realized_pnl"
        case realizedRMultiple = "realized_r_multiple"
        case createdAt = "created_at"
    }
}

private struct PlanAnalysis: Decodable {
    let planStatus: String
    let effectiveRiskBudget: String?
    let maximumWholeShares: Int?
    let bindingConstraint: String?
    let rewardRisk: String?
    let meetsRewardRiskFloor: Bool?
    let maxLoss: String?
    let maxProfit: String?
    let maxProfitLabel: String?
    let breakeven: String?
    let breakevens: [String]?
    let netDebit: String?
    let netPremiumLabel: String?
    let dataFreshness: String
    let disclaimer: String

    enum CodingKeys: String, CodingKey {
        case disclaimer
        case planStatus = "plan_status"
        case effectiveRiskBudget = "effective_risk_budget"
        case maximumWholeShares = "maximum_whole_shares"
        case bindingConstraint = "binding_constraint"
        case rewardRisk = "reward_risk"
        case meetsRewardRiskFloor = "meets_reward_risk_floor"
        case maxLoss = "max_loss"
        case maxProfit = "max_profit"
        case maxProfitLabel = "max_profit_label"
        case breakeven, breakevens
        case netDebit = "net_debit"
        case netPremiumLabel = "net_premium_label"
        case dataFreshness = "data_freshness"
    }

    var bindingConstraintLabel: String {
        switch bindingConstraint {
        case "max_position": "Position cap"
        case "daily_loss_limit": "Daily loss cap"
        case "risk_per_trade": "Per-trade risk"
        default: "Your limits"
        }
    }
}

private struct JournalEntry: Decodable, Identifiable {
    let id: String
    let symbol: String
    let kind: String
    let setupTag: String
    let title: String
    let body: String
    let outcome: String
    let disciplineScore: Int?
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, symbol, kind, title, body, outcome
        case setupTag = "setup_tag"
        case disciplineScore = "discipline_score"
        case createdAt = "created_at"
    }
}

private struct ReviewStats: Decodable {
    let entries: Int
    let reviews: Int
    let resolvedReviews: Int
    let win: Int
    let loss: Int
    let scratch: Int
    let open: Int
    let winRatePercent: String?
    let averageDisciplineScore: String?
    let setupCounts: [SetupCount]
    let scope: String

    enum CodingKeys: String, CodingKey {
        case entries, reviews, win, loss, scratch, open, scope
        case resolvedReviews = "resolved_reviews"
        case winRatePercent = "win_rate_percent"
        case averageDisciplineScore = "average_discipline_score"
        case setupCounts = "setup_counts"
    }

    static let empty = ReviewStats(
        entries: 0,
        reviews: 0,
        resolvedReviews: 0,
        win: 0,
        loss: 0,
        scratch: 0,
        open: 0,
        winRatePercent: nil,
        averageDisciplineScore: nil,
        setupCounts: [],
        scope: "Self-recorded journal outcomes."
    )
}

private struct SetupCount: Decodable {
    let tag: String
    let count: Int
}

private struct WatchPayload: Encodable { let symbol: String }

private struct DecisionSettingsPayload: Encodable {
    let autoRefreshEnabled: Bool
    let refreshIntervalHours: Int

    enum CodingKeys: String, CodingKey {
        case autoRefreshEnabled = "auto_refresh_enabled"
        case refreshIntervalHours = "refresh_interval_hours"
    }
}

private struct DecisionRefreshResult: Decodable {
    let completed: Int
    let failed: Int
}

private struct PortfolioImportPayload: Encodable {
    let filename: String
    let csvText: String

    enum CodingKeys: String, CodingKey {
        case filename
        case csvText = "csv_text"
    }
}

private struct PortfolioImportResult: Decodable {
    let id: String
    let rowCount: Int

    enum CodingKeys: String, CodingKey {
        case id
        case rowCount = "row_count"
    }
}

private struct DeleteAccountPayload: Encodable {
    let password: String
    let confirmation: String
}

private struct ChangePasswordPayload: Encodable {
    let currentPassword: String
    let newPassword: String

    enum CodingKeys: String, CodingKey {
        case currentPassword = "current_password"
        case newPassword = "new_password"
    }
}

private struct LogoutAllPayload: Encodable {
    let currentPassword: String

    enum CodingKeys: String, CodingKey {
        case currentPassword = "current_password"
    }
}

private struct AccountSecurityResponse: Decodable {
    let passwordChanged: Bool?
    let reauthRequired: Bool?
    let loggedOutAll: Bool?

    enum CodingKeys: String, CodingKey {
        case passwordChanged = "password_changed"
        case reauthRequired = "reauth_required"
        case loggedOutAll = "logged_out_all"
    }
}

private struct MarketConfiguration: Decodable {
    let provider: String
    let configured: Bool
}

private struct MarketConfigurationPayload: Encodable {
    let apiKey: String

    enum CodingKeys: String, CodingKey {
        case apiKey = "api_key"
    }
}

private struct AuthPayload: Encodable {
    let client: String
    let deviceID: String
    let deviceName: String
    let displayName: String?
    let email: String
    let password: String

    enum CodingKeys: String, CodingKey {
        case client, email, password
        case deviceID = "device_id"
        case deviceName = "device_name"
        case displayName = "display_name"
    }
}

private struct DevicePayload: Encodable {
    let deviceID: String
    let name: String
    let platform: String

    enum CodingKeys: String, CodingKey {
        case name, platform
        case deviceID = "device_id"
    }
}

private struct SyncAcknowledgementPayload: Encodable {
    let deviceID: String
    let revision: Int

    enum CodingKeys: String, CodingKey {
        case revision
        case deviceID = "device_id"
    }
}

private struct EmptyPayload: Encodable {}

private struct InvestorProfilePayload: Encodable {
    let strategyStyle: String
    let timeHorizon: String
    let paperAccountSize: String
    let maxPositionPercent: String
    let riskPerTradePercent: String
    let minimumRewardRisk: String
    let dailyLossLimit: String
    let optionsDefinedRiskOnly: Bool

    enum CodingKeys: String, CodingKey {
        case strategyStyle = "strategy_style"
        case timeHorizon = "time_horizon"
        case paperAccountSize = "paper_account_size"
        case maxPositionPercent = "max_position_percent"
        case riskPerTradePercent = "risk_per_trade_percent"
        case minimumRewardRisk = "minimum_reward_risk"
        case dailyLossLimit = "daily_loss_limit"
        case optionsDefinedRiskOnly = "options_defined_risk_only"
    }
}

private struct DeviceRecord: Decodable, Identifiable {
    let id: String
    let name: String?
    let platform: String?
    let lastRevision: Int?
    let lastSeenAt: String?

    enum CodingKeys: String, CodingKey {
        case id, name, platform
        case lastRevision = "last_revision"
        case lastSeenAt = "last_seen_at"
    }
}

private struct InvestorProfile: Decodable {
    let strategyStyle: String
    let timeHorizon: String
    let paperAccountSize: String
    let maxPositionPercent: String
    let riskPerTradePercent: String
    let minimumRewardRisk: String
    let dailyLossLimit: String
    let optionsDefinedRiskOnly: Bool
    let updatedAt: String
    let scope: String

    enum CodingKeys: String, CodingKey {
        case scope
        case strategyStyle = "strategy_style"
        case timeHorizon = "time_horizon"
        case paperAccountSize = "paper_account_size"
        case maxPositionPercent = "max_position_percent"
        case riskPerTradePercent = "risk_per_trade_percent"
        case minimumRewardRisk = "minimum_reward_risk"
        case dailyLossLimit = "daily_loss_limit"
        case optionsDefinedRiskOnly = "options_defined_risk_only"
        case updatedAt = "updated_at"
    }

    static let `default` = InvestorProfile(
        strategyStyle: "balanced",
        timeHorizon: "swing",
        paperAccountSize: "25000",
        maxPositionPercent: "10",
        riskPerTradePercent: "0.5",
        minimumRewardRisk: "2",
        dailyLossLimit: "300",
        optionsDefinedRiskOnly: true,
        updatedAt: "",
        scope: "User-supplied planning defaults."
    )
}

private struct SyncAcknowledgement: Decodable {
    let revision: Int
}

private struct LogoutResponse: Decodable {
    let loggedOut: Bool

    enum CodingKeys: String, CodingKey {
        case loggedOut = "logged_out"
    }
}

private struct DeleteResponse: Decodable { let deleted: Bool }

private struct TradePayload: Encodable {
    let symbol: String
    let assetType: String
    let side: String
    let quantity: String
    let price: String

    enum CodingKeys: String, CodingKey {
        case symbol, side, quantity, price
        case assetType = "asset_type"
    }
}

private struct PriceAlertPayload: Encodable {
    let symbol: String
    let direction: String
    let threshold: String
}

private struct JournalEntryPayload: Encodable {
    let symbol: String
    let kind: String
    let setupTag: String
    let title: String
    let body: String
    let outcome: String
    let disciplineScore: String

    enum CodingKeys: String, CodingKey {
        case symbol, kind, title, body, outcome
        case setupTag = "setup_tag"
        case disciplineScore = "discipline_score"
    }
}

private struct PlanReviewPayload: Encodable {
    let decision: String
    let outcome: String
    let disciplineScore: String
    let note: String
    let actualEntry: String
    let actualExit: String
    let screenshotDataURL: String
    let executionNote: String

    enum CodingKeys: String, CodingKey {
        case decision, outcome, note
        case disciplineScore = "discipline_score"
        case actualEntry = "actual_entry"
        case actualExit = "actual_exit"
        case screenshotDataURL = "screenshot_data_url"
        case executionNote = "execution_note"
    }
}

private struct DayPlanPayload: Encodable {
    let symbol: String
    let direction: String
    let hypothesis: String
    let accountSize: String
    let entry: String
    let stop: String
    let target: String
    let riskPercent: String
    let maxPositionPercent: String
    let dailyLossLimit: String
    let currentDailyLoss: String
    let minimumRewardRisk: String
    let premarketHigh: String
    let premarketLow: String
    let vwap: String
    let openingRangeHigh: String
    let openingRangeLow: String
    let support: String
    let resistance: String
    let haltStatus: String
    let setupKey: String

    enum CodingKeys: String, CodingKey {
        case symbol, direction, hypothesis, entry, stop, target, vwap, support, resistance
        case accountSize = "account_size"
        case riskPercent = "risk_percent"
        case maxPositionPercent = "max_position_percent"
        case dailyLossLimit = "daily_loss_limit"
        case currentDailyLoss = "current_daily_loss"
        case minimumRewardRisk = "minimum_reward_risk"
        case premarketHigh = "premarket_high"
        case premarketLow = "premarket_low"
        case openingRangeHigh = "opening_range_high"
        case openingRangeLow = "opening_range_low"
        case haltStatus = "halt_status"
        case setupKey = "setup_key"
    }
}

private struct RealtimeConfigurationPayload: Encodable {
    let apiKeyID: String
    let apiSecretKey: String
    enum CodingKeys: String, CodingKey {
        case apiKeyID = "api_key_id"
        case apiSecretKey = "api_secret_key"
    }
}

private struct DataSourceTestPayload: Encodable {
    let source: String
    let symbol: String
}

private struct StrategyTemplatePayload: Encodable {
    let name: String
    let technicalWeight: String
    let fundamentalWeight: String
    let valuationWeight: String
    let portfolioWeight: String
    let feeSlippageBps: String
    let activate: Bool
    enum CodingKeys: String, CodingKey {
        case name, activate
        case technicalWeight = "technical_weight"
        case fundamentalWeight = "fundamental_weight"
        case valuationWeight = "valuation_weight"
        case portfolioWeight = "portfolio_weight"
        case feeSlippageBps = "fee_slippage_bps"
    }
}

private struct RebalancePayload: Encodable { let targets: [RebalanceTargetPayload] }
private struct RebalanceTargetPayload: Encodable {
    let symbol: String
    let targetPercent: String
    enum CodingKeys: String, CodingKey {
        case symbol
        case targetPercent = "target_percent"
    }
}

private struct OptionPlanPayload: Encodable {
    let symbol: String
    let strategy: String
    let hypothesis: String
    let expiration: String
    let quantity: String
    let primaryStrike: String
    let primaryPremium: String
    let secondaryStrike: String
    let secondaryPremium: String
    let tertiaryStrike: String
    let tertiaryPremium: String
    let quaternaryStrike: String
    let quaternaryPremium: String

    enum CodingKeys: String, CodingKey {
        case symbol, strategy, hypothesis, expiration, quantity
        case primaryStrike = "primary_strike"
        case primaryPremium = "primary_premium"
        case secondaryStrike = "secondary_strike"
        case secondaryPremium = "secondary_premium"
        case tertiaryStrike = "tertiary_strike"
        case tertiaryPremium = "tertiary_premium"
        case quaternaryStrike = "quaternary_strike"
        case quaternaryPremium = "quaternary_premium"
    }
}

private struct ResearchCommandCenter: Decodable {
    let generatedAt: String
    let paperExecution: PaperOrderControl
    let scope: String

    enum CodingKeys: String, CodingKey {
        case scope
        case generatedAt = "generated_at"
        case paperExecution = "paper_execution"
    }

    func replacing(control: PaperOrderControl) -> ResearchCommandCenter {
        ResearchCommandCenter(generatedAt: generatedAt, paperExecution: control, scope: scope)
    }
}

private struct PaperOrderControl: Decodable {
    let enabled: Bool
    let maxOrderNotional: String
    let dailyLossLimit: String
    let updatedAt: String?
    let recordedLossToday: String
    let stopTriggered: Bool
    let realAccountSupported: Bool
    let scope: String

    enum CodingKeys: String, CodingKey {
        case enabled, scope
        case maxOrderNotional = "max_order_notional"
        case dailyLossLimit = "daily_loss_limit"
        case updatedAt = "updated_at"
        case recordedLossToday = "recorded_loss_today"
        case stopTriggered = "stop_triggered"
        case realAccountSupported = "real_account_supported"
    }
}

private struct PaperOrderControlSummary: Decodable {
    let enabled: Bool
    let maxOrderNotional: String
    let dailyLossLimit: String
    enum CodingKeys: String, CodingKey {
        case enabled
        case maxOrderNotional = "max_order_notional"
        case dailyLossLimit = "daily_loss_limit"
    }
}

private struct PaperOrderLedger: Decodable {
    let control: PaperOrderControlSummary
    let orders: [RoutedPaperOrder]
}

private struct RoutedPaperOrder: Decodable, Identifiable {
    let id: String
    let clientOrderID: String
    let brokerOrderID: String?
    let symbol: String
    let side: String
    let orderType: String
    let timeInForce: String
    let quantity: String
    let limitPrice: String?
    let stopPrice: String?
    let estimatedNotional: String
    let status: String
    let createdAt: String
    let updatedAt: String

    var isCancelable: Bool {
        !["filled", "canceled", "rejected", "failed"].contains(status)
    }

    enum CodingKeys: String, CodingKey {
        case id, symbol, side, quantity, status
        case clientOrderID = "client_order_id"
        case brokerOrderID = "broker_order_id"
        case orderType = "order_type"
        case timeInForce = "time_in_force"
        case limitPrice = "limit_price"
        case stopPrice = "stop_price"
        case estimatedNotional = "estimated_notional"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

private struct ScannerPreset: Decodable, Identifiable {
    let id: String
    let name: String
}

private struct UniverseScan: Decodable {
    let universeSize: Int
    let matched: Int
    let rows: [UniverseScanRow]
    let scope: String
    enum CodingKeys: String, CodingKey {
        case matched, rows, scope
        case universeSize = "universe_size"
    }
}

private struct UniverseScanRow: Decodable, Identifiable {
    let symbol: String
    let signalLabel: String
    let score: Int?
    let close: String
    let freshness: String
    var id: String { symbol }
    enum CodingKeys: String, CodingKey {
        case symbol, score, close, freshness
        case signalLabel = "signal_label"
    }
}

private struct NotificationRuleCenter: Decodable {
    let rules: [NotificationRule]
    let operationalAlerts: [NotificationRule]
    let activeCount: Int
    let scope: String
    enum CodingKeys: String, CodingKey {
        case rules, scope
        case operationalAlerts = "operational_alerts"
        case activeCount = "active_count"
    }
}

private struct NotificationRule: Decodable, Identifiable {
    let id: String
    let kind: String
    let symbol: String?
    let enabled: Bool
    let active: Bool
    let detail: String
}

private struct OptionScenarioResult: Decodable {
    let sampledMaxProfit: String
    let sampledMaxLoss: String
    let modeledDeltaShares: String
    let modeledThetaPerDay: String
    let breakevens: [String]
    let assignmentRisk: Bool
    let scope: String
    enum CodingKeys: String, CodingKey {
        case breakevens, scope
        case sampledMaxProfit = "sampled_max_profit"
        case sampledMaxLoss = "sampled_max_loss"
        case modeledDeltaShares = "modeled_delta_shares"
        case modeledThetaPerDay = "modeled_theta_per_day"
        case assignmentRisk = "assignment_risk"
    }
}

private struct StrategyComparisonResult: Decodable {
    let symbol: String
    let comparisons: [StrategyComparisonItem]
    let leaderVersionID: String?
    let selectionRule: String?

    enum CodingKeys: String, CodingKey {
        case symbol, comparisons
        case leaderVersionID = "leader_version_id"
        case selectionRule = "selection_rule"
    }
}

private struct StrategyComparisonItem: Decodable, Identifiable {
    let versionID: String
    let name: String
    let versionNumber: Int
    let available: Bool
    let strategyReturnPercent: String?
    let maxDrawdownPercent: String?
    let winRatePercent: String?
    let completedTrades: Int
    let outOfSampleAvailable: Bool
    let outOfSampleSessions: Int
    let outOfSampleReturnPercent: String?
    let outOfSampleReason: String?
    let reason: String?
    var id: String { versionID }
    enum CodingKeys: String, CodingKey {
        case name, available, reason
        case versionID = "version_id"
        case versionNumber = "version_number"
        case strategyReturnPercent = "strategy_return_percent"
        case maxDrawdownPercent = "max_drawdown_percent"
        case winRatePercent = "win_rate_percent"
        case completedTrades = "completed_trades"
        case outOfSampleAvailable = "out_of_sample_available"
        case outOfSampleSessions = "out_of_sample_sessions"
        case outOfSampleReturnPercent = "out_of_sample_return_percent"
        case outOfSampleReason = "out_of_sample_reason"
    }
}

private struct PortfolioIntelligence: Decodable {
    let grossExposure: String
    let cashEstimate: String
    let investedPercent: String
    let largestPositionPercent: String
    let scope: String
    enum CodingKeys: String, CodingKey {
        case scope
        case grossExposure = "gross_exposure"
        case cashEstimate = "cash_estimate"
        case investedPercent = "invested_percent"
        case largestPositionPercent = "largest_position_percent"
    }
}

private struct CommandDataQuality: Decodable {
    let summary: CommandDataQualitySummary
}

private struct CommandDataQualitySummary: Decodable {
    let symbols: Int
    let dailyBars: Int
    let staleSymbols: Int
    let recentFailedRuns: Int
    let intradayMissingMinutes: Int?
    let partialIntradaySessions: Int?
    let optionCrossedMarkets: Int?
    let optionWideSpreads: Int?
    enum CodingKeys: String, CodingKey {
        case symbols
        case dailyBars = "daily_bars"
        case staleSymbols = "stale_symbols"
        case recentFailedRuns = "recent_failed_runs"
        case intradayMissingMinutes = "intraday_missing_minutes"
        case partialIntradaySessions = "partial_intraday_sessions"
        case optionCrossedMarkets = "option_crossed_markets"
        case optionWideSpreads = "option_wide_spreads"
    }
}

private struct ResearchReport: Decodable, Identifiable {
    let id: String
    let period: String
    let reportDate: String
    let headline: String
    let summary: String
    enum CodingKeys: String, CodingKey {
        case id, period, headline, summary
        case reportDate = "report_date"
    }
}

private struct ResearchCopilotResult: Decodable {
    let symbol: String
    let answer: String
    let thesis: [String]
    let counterThesis: [String]
    let scope: String
    enum CodingKeys: String, CodingKey {
        case symbol, answer, thesis, scope
        case counterThesis = "counter_thesis"
    }
}

private struct PaperOrderControlPayload: Encodable {
    let enabled: Bool
    let maxOrderNotional: String
    let dailyLossLimit: String
    let acknowledged: Bool
    enum CodingKeys: String, CodingKey {
        case enabled, acknowledged
        case maxOrderNotional = "max_order_notional"
        case dailyLossLimit = "daily_loss_limit"
    }
}

private struct PaperOrderPayload: Encodable {
    let symbol: String
    let side: String
    let orderType: String
    let timeInForce: String
    let quantity: String
    let limitPrice: String?
    let stopPrice: String?
    let clientOrderID: String
    let acknowledged: Bool
    enum CodingKeys: String, CodingKey {
        case symbol, side, quantity, acknowledged
        case orderType = "order_type"
        case timeInForce = "time_in_force"
        case limitPrice = "limit_price"
        case stopPrice = "stop_price"
        case clientOrderID = "client_order_id"
    }
}

private struct PaperOrderConfirmationPayload: Encodable { let confirmation: String }

private struct ScannerPresetPayload: Encodable {
    let name: String
    let symbols: [String]
    let filters: ScannerFiltersPayload
}

private struct ScannerFiltersPayload: Encodable {
    let minimumScore: String
    enum CodingKeys: String, CodingKey { case minimumScore = "minimum_score" }
}

private struct UniverseScanPayload: Encodable {
    let presetID: String
    enum CodingKeys: String, CodingKey { case presetID = "preset_id" }
}

private struct NotificationRulePayload: Encodable {
    let kind: String
    let symbol: String?
    let config: NotificationRuleConfigPayload
}

private struct NotificationRuleConfigPayload: Encodable {
    let threshold: String
    let signal: String
}

private struct OptionScenarioPayload: Encodable {
    let spot: String
    let daysToExpiration: String
    let ivShiftPercent: String
    let legs: [OptionScenarioLegPayload]
    enum CodingKeys: String, CodingKey {
        case spot, legs
        case daysToExpiration = "days_to_expiration"
        case ivShiftPercent = "iv_shift_percent"
    }
}

private struct OptionScenarioLegPayload: Encodable {
    let right: String
    let side: String
    let strike: String
    let premium: String
    let quantity: Int
}

private struct ResearchCopilotPayload: Encodable {
    let symbol: String
    let question: String
}

private struct ResearchReportPayload: Encodable { let period: String }

private struct DatabaseRestorePayload: Encodable {
    let filename: String
    let confirmation: String
}

private struct ServerError: Decodable { let error: String }

private struct LabError: LocalizedError {
    let message: String
    let status: Int?
    init(_ message: String, status: Int? = nil) {
        self.message = message
        self.status = status
    }
    var errorDescription: String? { message }
}

private extension String {
    var decimal: Decimal { Decimal(string: self) ?? 0 }
    var currency: String {
        Self.currencyFormatter.string(from: decimal as NSDecimalNumber) ?? "$0.00"
    }

    static let currencyFormatter: NumberFormatter = {
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = "USD"
        return formatter
    }()
}
