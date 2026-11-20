// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "BaristaDesk",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(
            name: "BaristaDesk",
            path: "Sources/BaristaDesk",
            swiftSettings: [.swiftLanguageMode(.v5)],
            // nhúng Info.plist vào file chạy: chạy bằng `swift run` vẫn có mô tả quyền micro (không có thì macOS đóng app khi hỏi quyền)
            linkerSettings: [.unsafeFlags(["-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist", "-Xlinker", "Info.plist"])]
        ),
        .testTarget(name: "BaristaDeskTests", dependencies: ["BaristaDesk"], path: "Tests/BaristaDeskTests"),
    ]
)
