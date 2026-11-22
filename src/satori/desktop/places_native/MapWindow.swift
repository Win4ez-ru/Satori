import AppKit
import MapKit

@MainActor
private final class PlacesOwnedWindow: NSWindow {
    var onOrderOut: (() -> Void)?

    override func orderOut(_ sender: Any?) {
        super.orderOut(sender)
        onOrderOut?()
    }
}

/// View-only Apple map surface. Search, grants, receipts, TTL and route admission belong to the caller.
@MainActor
final class PlacesMapWindow: NSObject, NSWindowDelegate {
    private let onClose: () -> Void
    private let onChooseArea: (String) -> Void
    private let onRoute: (String, String) -> Void
    private var window: NSWindow?
    private var map: MKMapView?
    private var input: SearchInput?
    private var currentReceiptID: String?
    private var currentExpiry: Date?
    private var closeNotified = false
    private var closing = false
    private var hideObserver: NSObjectProtocol?
    private var areaActions: [ObjectIdentifier: String] = [:]
    private var routeActions: [ObjectIdentifier: (receiptID: String, placeID: String)] = [:]
    private let heading = NSTextField(wrappingLabelWithString: "Места на карте")
    private let queryLabel = NSTextField(wrappingLabelWithString: "")
    private let originLabel = NSTextField(wrappingLabelWithString: "")
    private let statusLabel = NSTextField(wrappingLabelWithString: "")
    private let mapContainer = NSView()
    private let cards = NSStackView()

    /// Reads this owned window only and confirms that its corresponding Apple map is mounted.
    var isPresenting: Bool {
        guard let window else { return false }
        return window.isVisible && !window.isMiniaturized && map?.superview === mapContainer
    }

    init(
        onClose: @escaping () -> Void,
        onChooseArea: @escaping (String) -> Void,
        onRoute: @escaping (String, String) -> Void
    ) {
        self.onClose = onClose
        self.onChooseArea = onChooseArea
        self.onRoute = onRoute
        super.init()
        // No window, MKMapView, observer, source request or acquisition is created here.
    }

    func showSearching(_ input: SearchInput) {
        guard input.valid() else { clear("Поисковый запрос отклонён."); return }
        resetContent()
        self.input = input
        closeNotified = false
        makeWindowIfNeeded()
        heading.stringValue = "\(bounded(input.city, 120)) · \(bounded(input.district, 180))"
        queryLabel.stringValue = "Запрос: \(bounded(input.query, 240)) · радиус \(input.radiusMeters) м"
        originLabel.stringValue = input.explicitCenter.map {
            "Явно выбранная точка: \(coordinateText($0)). Геопозиция устройства не запрашивается."
        } ?? "Точка поиска уточняется по указанным городу и району. Геопозиция устройства не запрашивается."
        statusLabel.stringValue = "Ищу в Apple Maps… Карта и результаты появятся только для текущего запроса."
        showPlaceholder("Ожидаю результат поиска.")
        window?.makeKeyAndOrderFront(nil)
        // Explicit Search only; the helper remains inert on construction/startup.
        NSApplication.shared.activate()
    }

    func showResult(_ output: SearchOutput) {
        guard let input, let window, window.isVisible, !window.isMiniaturized,
              !closeNotified, output.schemaVersion == 1,
              output.requestID == input.requestID, output.sessionID == input.sessionID,
              output.epoch == input.epoch else { return }
        resetContent(keepInput: true)
        heading.stringValue = "\(bounded(output.city, 120)) · \(bounded(output.district, 180))"
        queryLabel.stringValue = "Запрос: \(bounded(output.query, 240)) · радиус \(input.radiusMeters) м"
        let choices = Array(output.areaChoices.prefix(5)).filter { $0.coordinate.valid }
        let places = Array(output.places.prefix(20)).filter { $0.coordinate.valid }
        let origin = output.origin.flatMap { $0.valid ? $0 : nil }
        if let origin {
            originLabel.stringValue = "\(output.originConfirmed ? "Выбранная" : "Неподтверждённая") точка: \(bounded(output.originLabel ?? "Точка поиска", 180)) · \(coordinateText(origin)). GPS не используется."
        } else {
            originLabel.stringValue = "Точка поиска не выбрана. Для маршрута сначала выбери точку или район. GPS не используется."
        }
        let statusText = ["complete": "Поиск завершён", "partial": "Получена часть результатов", "needs_origin_confirmation": "Выбери точку поиска", "unavailable": "Сервис недоступен"] [output.status] ?? "Нет текущего результата"
        statusLabel.stringValue = "\(statusText) · Apple Maps · получено: \(bounded(output.observedAt, 80)) · актуально до: \(bounded(output.expiresAt, 80))"
        let coordinates = (origin.map { [$0] } ?? []) + choices.map(\.coordinate) + places.map(\.coordinate)
        guard !coordinates.isEmpty else {
            showPlaceholder("Нет координат для отображения.\nКарта и маршруты недоступны.")
            if let errorCode = output.errorCode {
                cards.addArrangedSubview(label("Поиск не завершён: \(bounded(errorCode, 100)). Пустой ответ не доказывает отсутствие мест."))
            }
            return
        }
        // Mount the corresponding Apple map before rendering any place or area cards.
        installMap(coordinates: coordinates, origin: origin)
        currentReceiptID = output.receiptID
        currentExpiry = ISO8601DateFormatter().date(from: output.expiresAt)
        let actionable = !output.receiptID.isEmpty && currentExpiry.map { $0 > Date() } == true
        for choice in choices {
            let card = cardStack()
            card.addArrangedSubview(label(bounded(choice.label, 180), bold: true))
            card.addArrangedSubview(label("Точка: \(coordinateText(choice.coordinate))"))
            let button = NSButton(title: "Выбрать этот район / точку", target: self, action: #selector(chooseArea(_:)))
            button.isEnabled = actionable && !choice.choiceID.isEmpty
            areaActions[ObjectIdentifier(button)] = choice.choiceID
            card.addArrangedSubview(button)
            addCard(card)
            addPin(choice.coordinate, title: bounded(choice.label, 100))
        }
        if !choices.isEmpty {
            addCard(label("Выбери подходящую точку явно. Совпадение названия не подтверждает нужный район."))
        }
        for place in places {
            let card = cardStack()
            card.addArrangedSubview(label(bounded(place.name, 160), bold: true))
            card.addArrangedSubview(label(bounded(place.address, 240)))
            let distance = max(0, min(place.distanceMeters, 20_000_000))
            card.addArrangedSubview(label("≈ \(distance) м по прямой от выбранной точки"))
            card.addArrangedSubview(label("Кухня: неизвестно · Сейчас открыто: неизвестно · Бюджет: неизвестно"))
            card.addArrangedSubview(label("Совпадение с запросом Apple не подтверждает кухню, часы работы или бюджет. Получено: \(bounded(output.observedAt, 80))."))
            let route = NSButton(title: "Открыть маршрут от выбранной точки", target: self, action: #selector(route(_:)))
            route.isEnabled = actionable && output.originConfirmed && origin != nil && !place.placeID.isEmpty
            routeActions[ObjectIdentifier(route)] = (output.receiptID, place.placeID)
            card.addArrangedSubview(route)
            addCard(card)
            addPin(place.coordinate, title: bounded(place.name, 100))
        }
        if choices.isEmpty && places.isEmpty {
            addCard(label("В этом ответе нет мест. Полнота поиска и отсутствие подходящих заведений не подтверждены."))
        }
        if output.areaChoices.count > 5 || output.places.count > 20 {
            addCard(label("Показана ограниченная часть ответа: не более 5 вариантов района и 20 мест."))
        }
        window.displayIfNeeded()
    }

    func clear(_ message: String) {
        resetContent()
        heading.stringValue = "Места на карте"
        queryLabel.stringValue = ""
        originLabel.stringValue = "Доступ к данным текущего запроса закрыт."
        statusLabel.stringValue = bounded(message, 300)
        if window != nil { showPlaceholder("Карта и карточки очищены.") }
    }

    func close() {
        guard !closing else { return }
        closing = true
        clear("Окно закрыто.")
        notifyCloseOnce()
        window?.close()
        closing = false
    }

    func windowWillClose(_ notification: Notification) {
        let wasClosing = closing
        closing = true
        clear("Окно закрыто.")
        notifyCloseOnce()
        closing = wasClosing
    }

    func windowDidMiniaturize(_ notification: Notification) {
        clear("Окно свёрнуто: карта и карточки очищены.")
        notifyCloseOnce()
    }

    private func notifyCloseOnce() {
        guard !closeNotified else { return }
        closeNotified = true
        onClose()
    }

    private func makeWindowIfNeeded() {
        guard window == nil else { return }
        let window = PlacesOwnedWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1120, height: 720),
            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false
        )
        window.title = "Сатори · Места на карте"
        window.minSize = NSSize(width: 900, height: 640)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.onOrderOut = { [weak self] in
            self?.clear("Окно скрыто: карта и карточки очищены.")
            self?.notifyCloseOnce()
        }
        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 10
        root.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        heading.font = .boldSystemFont(ofSize: 17)
        for field in [heading, queryLabel, originLabel, statusLabel] {
            field.isSelectable = true
            field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            root.addArrangedSubview(field)
            field.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -32).isActive = true
        }
        let disclosure = label("Карта Apple загружает данные по сети. Исходная точка задана явно; геопозиция устройства не запрашивается. Маршрут откроется только по отдельной кнопке; он передаёт выбранную исходную и конечную точки внешнему сервису.")
        root.addArrangedSubview(disclosure)
        disclosure.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -32).isActive = true
        let row = NSStackView()
        row.orientation = .horizontal
        row.alignment = .top
        row.spacing = 14
        mapContainer.translatesAutoresizingMaskIntoConstraints = false
        row.addArrangedSubview(mapContainer)
        cards.orientation = .vertical
        cards.alignment = .leading
        cards.spacing = 14
        cards.edgeInsets = NSEdgeInsets(top: 8, left: 8, bottom: 8, right: 8)
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.drawsBackground = false
        scroll.documentView = cards
        cards.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            cards.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
            cards.trailingAnchor.constraint(equalTo: scroll.contentView.trailingAnchor),
            cards.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
        ])
        row.addArrangedSubview(scroll)
        root.addArrangedSubview(row)
        window.contentView = root
        NSLayoutConstraint.activate([
            row.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -32),
            row.bottomAnchor.constraint(equalTo: root.bottomAnchor, constant: -16),
            row.heightAnchor.constraint(greaterThanOrEqualToConstant: 260),
            row.heightAnchor.constraint(lessThanOrEqualToConstant: 640),
            mapContainer.widthAnchor.constraint(equalTo: row.widthAnchor, multiplier: 0.48),
            mapContainer.heightAnchor.constraint(equalTo: row.heightAnchor),
            scroll.heightAnchor.constraint(equalTo: row.heightAnchor),
        ])
        self.window = window
        window.center()
        hideObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didHideNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.clear("Приложение скрыто: карта и карточки очищены.")
                self?.notifyCloseOnce()
            }
        }
    }

    private func resetContent(keepInput: Bool = false) {
        if !keepInput { input = nil }
        currentReceiptID = nil
        currentExpiry = nil
        areaActions.removeAll()
        routeActions.removeAll()
        map?.delegate = nil
        map?.removeFromSuperview()
        map = nil
        for view in mapContainer.subviews { view.removeFromSuperview() }
        for view in cards.arrangedSubviews {
            cards.removeArrangedSubview(view)
            view.removeFromSuperview()
        }
    }

    private func installMap(coordinates: [Coordinate], origin: Coordinate?) {
        let map = MKMapView()
        map.showsUserLocation = false
        map.pointOfInterestFilter = .excludingAll
        map.translatesAutoresizingMaskIntoConstraints = false
        mapContainer.addSubview(map)
        NSLayoutConstraint.activate([
            map.leadingAnchor.constraint(equalTo: mapContainer.leadingAnchor),
            map.trailingAnchor.constraint(equalTo: mapContainer.trailingAnchor),
            map.topAnchor.constraint(equalTo: mapContainer.topAnchor),
            map.bottomAnchor.constraint(equalTo: mapContainer.bottomAnchor),
        ])
        let center = origin ?? coordinates[0]
        let radius = Double(input?.radiusMeters ?? 3000)
        map.setRegion(MKCoordinateRegion(
            center: CLLocationCoordinate2D(latitude: center.latitude, longitude: center.longitude),
            latitudinalMeters: radius * 2.2, longitudinalMeters: radius * 2.2
        ), animated: false)
        self.map = map
        if let origin { addPin(origin, title: "Выбранная исходная точка") }
        mapContainer.layoutSubtreeIfNeeded()
        window?.displayIfNeeded()
    }

    private func addPin(_ coordinate: Coordinate, title: String) {
        let pin = MKPointAnnotation()
        pin.coordinate = CLLocationCoordinate2D(latitude: coordinate.latitude, longitude: coordinate.longitude)
        pin.title = title
        map?.addAnnotation(pin)
    }

    private func showPlaceholder(_ value: String) {
        let field = label(value)
        field.translatesAutoresizingMaskIntoConstraints = false
        mapContainer.addSubview(field)
        NSLayoutConstraint.activate([
            field.centerXAnchor.constraint(equalTo: mapContainer.centerXAnchor),
            field.centerYAnchor.constraint(equalTo: mapContainer.centerYAnchor),
            field.widthAnchor.constraint(lessThanOrEqualTo: mapContainer.widthAnchor, constant: -24),
        ])
    }

    private func label(_ value: String, bold: Bool = false) -> NSTextField {
        let field = NSTextField(wrappingLabelWithString: value)
        field.font = bold ? .boldSystemFont(ofSize: 14) : .systemFont(ofSize: 12)
        field.isSelectable = true
        field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return field
    }

    private func cardStack() -> NSStackView {
        let card = NSStackView()
        card.orientation = .vertical
        card.alignment = .leading
        card.spacing = 5
        return card
    }

    private func addCard(_ view: NSView) {
        cards.addArrangedSubview(view)
        view.widthAnchor.constraint(equalTo: cards.widthAnchor, constant: -16).isActive = true
    }

    private func bounded(_ value: String, _ limit: Int) -> String {
        value.unicodeScalars.prefix(limit).map {
            CharacterSet.controlCharacters.contains($0) ? " " : String($0)
        }.joined()
    }

    private func coordinateText(_ coordinate: Coordinate) -> String {
        String(format: "%.6f, %.6f", coordinate.latitude, coordinate.longitude)
    }

    private func validVisibleAction(_ sender: NSButton) -> Bool {
        guard let window, window.isVisible, !window.isMiniaturized,
              !closeNotified,
              sender.window === window, sender.isEnabled,
              currentExpiry.map({ $0 > Date() }) == true else { return false }
        return true
    }

    @objc private func chooseArea(_ sender: NSButton) {
        guard validVisibleAction(sender), let choiceID = areaActions[ObjectIdentifier(sender)] else { return }
        areaActions.removeAll()
        routeActions.removeAll()
        sender.isEnabled = false
        onChooseArea(choiceID)
    }

    @objc private func route(_ sender: NSButton) {
        guard validVisibleAction(sender), let action = routeActions[ObjectIdentifier(sender)],
              currentReceiptID == action.receiptID else { return }
        routeActions.removeValue(forKey: ObjectIdentifier(sender))
        sender.isEnabled = false
        onRoute(action.receiptID, action.placeID)
    }
}
