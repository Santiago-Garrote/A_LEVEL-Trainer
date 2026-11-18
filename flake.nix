{
  description = "A-Level Trainer: CAIE past-paper pipeline + offline study PWA";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      python = pkgs: pkgs.python3.withPackages (p: [ p.pymupdf p.requests p.pillow p.playwright ]);
    in {
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ (python pkgs) pkgs.playwright-driver.browsers ];
          PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}";
          PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
        };
      });

      apps = forAll (pkgs:
        let
          script = name: text: {
            type = "app";
            program = "${pkgs.writeShellScript name text}";
          };
          publishScript = ''
            set -euo pipefail
            export PATH=${pkgs.lib.makeBinPath [ pkgs.gh pkgs.coreutils ]}:$PATH
            repo=''${PACKS_REPO:-Santiago-Garrote/A_LEVEL-Packs}
            cd data/packs
            # what the release already holds, as "name sha256:..." lines
            remote=$(gh api "repos/$repo/releases/tags/packs" --jq '.assets[] | "\(.name) \(.digest // "")"')
            changed=()
            for f in index.json *.zip; do
              if ! grep -qx "$f sha256:$(sha256sum "$f" | cut -d' ' -f1)" <<<"$remote"; then changed+=("$f"); fi
            done
            if [ ''${#changed[@]} -gt 0 ]; then
              echo "Uploading: ''${changed[*]}"
              gh release upload packs "''${changed[@]}" --repo "$repo" --clobber
            else
              echo "Release already up to date"
            fi
            # drop packs that were removed locally
            while read -r name _; do
              [ -n "$name" ] && [ ! -e "$name" ] && gh release delete-asset packs "$name" --repo "$repo" -y
            done <<<"$remote"
            gh workflow run pages.yml --repo "$repo"
            echo "Deploying: https://github.com/$repo/actions (live in about a minute)"
          '';
        in {
          # nix run .#papers -- all 9702
          papers = script "papers" ''exec ${python pkgs}/bin/python3 pipeline/papers.py "$@"'';
          # nix run .#serve   (http://localhost:8000 with packs at /packs/, add --bind 0.0.0.0 for LAN)
          serve = script "serve" ''exec ${python pkgs}/bin/python3 pipeline/serve.py "$@"'';
          # nix run .#publish   upload data/packs to the "packs" release of A_LEVEL-Packs and redeploy its Pages site
          publish = script "publish" publishScript;
          # nix run .#add -- 9708 [https://pastpapers.papacambridge.com/...] [--no-publish]
          # download + build + check + pack each subject (code = whole subject from 2018), then publish
          add = script "add" ''
            set -euo pipefail
            publish=1 args=()
            for a in "$@"; do [ "$a" = --no-publish ] && publish=0 || args+=("$a"); done
            if [ ''${#args[@]} -eq 0 ]; then
              echo "usage: nix run .#add -- <code or PapaCambridge link>... [--no-publish]"; exit 2
            fi
            ${python pkgs}/bin/python3 pipeline/papers.py add "''${args[@]}"
            if [ $publish = 1 ]; then
              ${pkgs.writeShellScript "publish" publishScript}
            else
              echo "Not published (--no-publish). Run: nix run .#publish"
            fi
          '';
          # nix run .#test
          test = script "test" ''
            export PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers}
            exec ${python pkgs}/bin/python3 tests/e2e.py "$@"
          '';
        });
    };
}
