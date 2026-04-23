class Stackowl < Formula
  desc "Personal AI assistant with multi-owl personalities, Parliament brainstorming, and Owl DNA evolution"
  homepage "https://github.com/starskrime/stackowl-personal-ai-assistant"
  version "0.1.0"

  depends_on "node"

  on_macos do
    if Hardware::CPU.arm?
      url "https://github.com/starskrime/stackowl-personal-ai-assistant/releases/download/v#{version}/stackowl-v#{version}-darwin-arm64.tar.gz"
      sha256 "REPLACE_WITH_SHA256_FROM_RELEASE_darwin-arm64"
    else
      url "https://github.com/starskrime/stackowl-personal-ai-assistant/releases/download/v#{version}/stackowl-v#{version}-darwin-x86_64.tar.gz"
      sha256 "REPLACE_WITH_SHA256_FROM_RELEASE_darwin-x86_64"
    end
  end

  on_linux do
    url "https://github.com/starskrime/stackowl-personal-ai-assistant/releases/download/v#{version}/stackowl-v#{version}-linux-x86_64.tar.gz"
    sha256 "REPLACE_WITH_SHA256_FROM_RELEASE_linux-x86_64"
  end

  def install
    libexec.install "lib/dist", "lib/node_modules"
    (libexec/"dist").rename(libexec/"dist")

    # Rewrite the launcher to point at libexec
    (bin/"stackowl").write <<~SH
      #!/bin/sh
      exec node "#{libexec}/dist/index.js" "$@"
    SH
    chmod 0755, bin/"stackowl"
  end

  def caveats
    <<~EOS
      On first run, StackOwl will ask you to configure your AI provider:
        stackowl

      Your config and data live in:
        ~/.stackowl/

      To re-run setup at any time, type /onboarding inside the chat.
    EOS
  end

  test do
    assert_match "stackowl", shell_output("#{bin}/stackowl --version 2>&1", 0)
  end
end
