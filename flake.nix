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
        in {
          # nix run .#papers -- all 9702
          papers = script "papers" ''exec ${python pkgs}/bin/python3 pipeline/papers.py "$@"'';
          # nix run .#serve   (http://localhost:8000 with packs at /packs/, add --bind 0.0.0.0 for LAN)
          serve = script "serve" ''exec ${python pkgs}/bin/python3 pipeline/serve.py "$@"'';
          # nix run .#test
          test = script "test" ''
            export PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers}
            exec ${python pkgs}/bin/python3 tests/e2e.py "$@"
          '';
        });
    };
}
