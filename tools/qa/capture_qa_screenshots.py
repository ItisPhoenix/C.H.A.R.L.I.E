import os
import sys
import time
import html
from pathlib import Path
from playwright.sync_api import sync_playwright

import argparse

parser = argparse.ArgumentParser(description="Phase 9 QA Screenshot Capture Harness")
parser.add_argument("--output-dir", type=str, default=None, help="Directory to save QA screenshots")
parser.add_argument("--url", type=str, default="http://127.0.0.1:5173", help="Base URL of Charlie frontend")
cli_args, _ = parser.parse_known_args()

if cli_args.output_dir:
    OUTPUT_DIR = Path(cli_args.output_dir).resolve()
elif os.environ.get("CHARLIE_QA_OUTPUT_DIR"):
    OUTPUT_DIR = Path(os.environ["CHARLIE_QA_OUTPUT_DIR"]).resolve()
else:
    brain_dir = Path(r"C:\Users\abhi2\.gemini\antigravity\brain\bea8e6b6-3620-42aa-9ba3-1aa4e326fcd9\after_qa")
    if brain_dir.parent.exists():
        OUTPUT_DIR = brain_dir
    else:
        OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "artifacts" / "qa"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
URL = cli_args.url

def run_qa_captures():
    print(f"=== Starting Phase 9 QA Screenshot Capture Harness ===")
    print(f"Target Output Directory: {OUTPUT_DIR}\n")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{URL}{'&' if '?' in URL else '?'}qa_fixture=1")
        page.wait_for_load_state("networkidle")
        time.sleep(2.0)

        def setup_state(script: str):
            page.evaluate(f"() => {{ {script} }}")
            time.sleep(0.8)

        def assert_and_capture(
            filename: str,
            expected_type: str = "none",
            expected_renderer: str = "None",
            content_keys: str = "",
            required_selectors_or_texts: list = None,
        ):
            print(f"\n[QA] Capturing: {filename}")
            print(f"[QA] type={expected_type}")
            print(f"[QA] contentKeys={content_keys}")
            print(f"[QA] renderer={expected_renderer}")

            # Check for invalid fallback DOM text
            page_text = html.unescape(page.content())
            if "No active stream details." in page_text:
                raise AssertionError(f"FAIL CAPTURE: '{filename}' rendered fallback 'No active stream details.'")
            
            # Check for generic custom workspace label
            if expected_type != "none" and "// custom" in page_text.lower():
                # Verify if it's the active workspace header
                custom_header = page.query_selector("text='WORKSPACE // CUSTOM'")
                if custom_header:
                    raise AssertionError(f"FAIL CAPTURE: '{filename}' rendered generic 'WORKSPACE // CUSTOM'")

            # Check required specific elements for this renderer
            if required_selectors_or_texts:
                for req in required_selectors_or_texts:
                    if req.startswith("[") or req.startswith(".") or req.startswith("#"):
                        if page.locator(req).first.count() == 0:
                            raise AssertionError(
                                f"FAIL CAPTURE: '{filename}' missing required selector: '{req}' for renderer '{expected_renderer}'"
                            )
                    else:
                        if req not in page_text:
                            raise AssertionError(
                                f"FAIL CAPTURE: '{filename}' missing required text: '{req}' in DOM for renderer '{expected_renderer}'"
                            )

            save_path = OUTPUT_DIR / filename
            page.screenshot(path=str(save_path))
            print(f"[QA] PASS -> Saved {save_path} ({os.path.getsize(save_path)} bytes)")

        # ----------------------------------------------------
        # 1. Idle HUD Centered
        # ----------------------------------------------------
        setup_state("""
            const { charlie, workspace, widget } = window.__CHARLIE_STORES__;
            workspace.getState().clearWorkspaces();
            widget.getState().clearScreen();
            charlie.setState({ coreState: 'idle', activeCaption: null, presentationIntents: {} });
        """)
        time.sleep(1.0)
        idle_main = page.locator("main.charlie-scene-root")
        idle_state = {
            "scene": idle_main.get_attribute("data-scene-mode"),
            "position": idle_main.get_attribute("data-core-position"),
            "core": idle_main.get_attribute("data-core-state"),
            "label": page.locator(".charlie-core-state-label").inner_text().strip(),
        }
        if idle_state != {"scene": "idle", "position": "center", "core": "idle", "label": "IDLE"}:
            raise AssertionError(f"FAIL CAPTURE: Idle fixture contamination: {idle_state}")
        assert_and_capture(
            "01_idle_1920x1080.png",
            expected_type="idle",
            expected_renderer="CharlieCore (Centered)",
            content_keys="none",
            required_selectors_or_texts=['.charlie-core-center']
        )

        # ----------------------------------------------------
        # 2. Listening State
        # ----------------------------------------------------
        setup_state("""
            const { charlie } = window.__CHARLIE_STORES__;
            charlie.setState({ coreState: 'listening', activeCaption: 'Listening for command...' });
        """)
        assert_and_capture(
            "02_listening_state.png",
            expected_type="listening",
            expected_renderer="CharlieCore + Caption",
            content_keys="coreState,activeCaption",
            required_selectors_or_texts=['Listening for command...', '[data-core-state="listening"]']
        )

        # ----------------------------------------------------
        # 3. Speaking / Caption State
        # ----------------------------------------------------
        setup_state("""
            const { charlie } = window.__CHARLIE_STORES__;
            charlie.setState({
                coreState: 'speaking',
                activeCaption: 'Analyzing telemetry data across 4 cluster nodes. All systems operational.'
            });
        """)
        assert_and_capture(
            "03_speaking_caption.png",
            expected_type="speaking",
            expected_renderer="CharlieCore + Caption",
            content_keys="coreState,activeCaption",
            required_selectors_or_texts=['Analyzing telemetry data across 4 cluster nodes.', '[data-core-state="speaking"]']
        )

        def wait_for_map_readiness(timeout_sec=15, require_route=False):
            start = time.time()
            while time.time() - start < timeout_sec:
                status = page.evaluate("""() => {
                    const map = window.__CHARLIE_MAP_INSTANCE__;
                    if (!map) return { ready: false };
                    const canvas = document.querySelector('.maplibregl-canvas');
                    if (!canvas || canvas.width <= 0 || canvas.height <= 0) return { ready: false };

                    const isStyleLoaded = typeof map.isStyleLoaded === 'function' ? map.isStyleLoaded() : true;
                    const isMapLoaded = typeof map.loaded === 'function' ? map.loaded() : true;
                    const areTilesLoaded = typeof map.areTilesLoaded === 'function' ? map.areTilesLoaded() : true;
                    const isMoving = typeof map.isMoving === 'function' ? map.isMoving() : false;
                    const hasLayers = Boolean(map.getStyle() && map.getStyle().layers && map.getStyle().layers.length > 0);

                    // Check WebGL Context validity
                    let isContextValid = false;
                    try {
                        const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
                        isContextValid = Boolean(gl && !gl.isContextLost());
                    } catch {
                        isContextValid = false;
                    }

                    // Basemap is genuinely renderable when map loaded, style loaded, tiles loaded, layers exist, and WebGL active
                    const basemapVisible = isStyleLoaded && isMapLoaded && areTilesLoaded && hasLayers && isContextValid;

                    // Verify route presence from actual active renderer (Deck.gl Tier A/B or MapLibre native Tier C)
                    const stores = window.__CHARLIE_STORES__;
                    const mapStore = stores?.map?.getState?.();
                    const hasRouteInStore = Boolean(mapStore?.route && ((mapStore.route.geometry && mapStore.route.geometry.length > 1) || (mapStore.route.coordinates && mapStore.route.coordinates.length > 1)));

                    let hasDeckRoute = false;
                    if (map._controls) {
                        for (const ctrl of map._controls) {
                            const layers = ctrl?._props?.layers || ctrl?.props?.layers || ctrl?._deck?.props?.layers || ctrl?._deck?.layerManager?.layers || ctrl?._layers || [];
                            if (Array.isArray(layers) && layers.some(l => l && (l.id === 'charlie-route-corridor' || l.id === 'charlie-route-endpoints' || (typeof l.id === 'string' && l.id.includes('route'))))) {
                                hasDeckRoute = true;
                                break;
                            }
                        }
                    }

                    // Check custom interleaved layers in map style if present
                    if (!hasDeckRoute && map.getStyle && map.getStyle()) {
                        const styleLayers = map.getStyle().layers || [];
                        if (styleLayers.some(l => l && (l.id === 'charlie-route-corridor' || l.id === 'charlie-route-endpoints' || (typeof l.id === 'string' && l.id.includes('charlie-route'))))) {
                            hasDeckRoute = true;
                        }
                    }

                    const nativeSource = typeof map.getSource === 'function' ? map.getSource('charlie-route-source') : null;
                    const hasNativeRoute = Boolean(
                        nativeSource &&
                        nativeSource._data &&
                        ((nativeSource._data.type === 'FeatureCollection' && Array.isArray(nativeSource._data.features) && nativeSource._data.features.length > 0) ||
                         (nativeSource._data.type === 'Feature' && nativeSource._data.geometry))
                    );

                    const routeVisibleInRenderer = hasRouteInStore && (hasDeckRoute || hasNativeRoute);

                    return {
                        ready: true,
                        styleLoaded: isStyleLoaded,
                        sourceLoaded: areTilesLoaded,
                        mapIdle: !isMoving,
                        basemapVisible: basemapVisible,
                        routeVisible: routeVisibleInRenderer,
                    };
                }""")

                if status.get("ready"):
                    style_loaded = status.get("styleLoaded", False)
                    source_loaded = status.get("sourceLoaded", False)
                    idle = status.get("mapIdle", False)
                    basemap_visible = status.get("basemapVisible", False)
                    route_visible = status.get("routeVisible", False)
                    route_ok = (not require_route) or route_visible

                    if style_loaded and idle and basemap_visible and route_ok:
                        time.sleep(0.8)
                        print(f"[QA MAP]\nstyleLoaded={str(style_loaded).lower()}\nsourceLoaded={str(source_loaded).lower()}\nmapIdle={str(idle).lower()}\nrouteVisible={str(route_visible).lower()}\nbasemapVisible={str(basemap_visible).lower()}")
                        return True
                time.sleep(0.3)

            raise TimeoutError(f"HARD QA FAIL: MapLibre/Deck.gl did not achieve verified readiness within {timeout_sec}s")

        # Reset core to idle
        setup_state("""
            const { charlie } = window.__CHARLIE_STORES__;
            charlie.setState({ coreState: 'idle', activeCaption: null });
        """)

        # ----------------------------------------------------
        # 4. Map Workspace - Default World View
        # ----------------------------------------------------
        setup_state("""
            const { workspace, map } = window.__CHARLIE_STORES__;
            map.getState().clearMap();
            workspace.getState().openWorkspace({
                id: 'ws_map',
                kind: 'workspace',
                title: 'SPATIAL INTELLIGENCE // GLOBAL',
                workspaceType: 'map',
                workspace_type: 'map',
                type: 'map',
                mode: 'workspace',
                target: 'workspace',
                summary: 'Real-time spatial intelligence and global infrastructure topology.',
                content: { mode: 'geo' }
            });
        """)
        wait_for_map_readiness(timeout_sec=15)
        assert_and_capture(
            "04_map_default_1920x1080.png",
            expected_type="map",
            expected_renderer="MapWorkspace -> MapEngine",
            content_keys="mode",
            required_selectors_or_texts=['.maplibregl-canvas', '3D']
        )

        # ----------------------------------------------------
        # 5. Map - Context Card / Selected Location
        # ----------------------------------------------------
        setup_state("""
            const { map } = window.__CHARLIE_STORES__;
            map.getState().setSelectedFeature({
                id: 'feat_tokyo',
                label: 'Tokyo Research Hub',
                category: 'Regional Datacenter',
                description: 'Active fiber uplink at 99.8% capacity with low latency.',
                coordinates: [139.69, 35.68],
                severity: 'normal'
            });
        """)
        wait_for_map_readiness(timeout_sec=12)
        assert_and_capture(
            "05_map_context_card.png",
            expected_type="map",
            expected_renderer="MapEngine + MapContextCard",
            content_keys="selectedFeature",
            required_selectors_or_texts=['Tokyo Research Hub', 'Regional Datacenter']
        )

        # ----------------------------------------------------
        # 6. Map - Geodesic Measurement Corridor
        #    route_kind=geodesic_measurement => MUST NOT show DRIVING/turn steps/driving duration
        #    distance MUST equal true spherical great-circle distance (~237.4 km, not 268.4 km)
        # ----------------------------------------------------
        setup_state("""
            const { map } = window.__CHARLIE_STORES__;
            map.getState().clearSelection();
            const routeData = {
                startCoordinates: [77.1025, 28.7041],
                startLabel: 'Delhi Core',
                destinationCoordinates: [75.7873, 26.9124],
                destinationLabel: 'Jaipur Relay',
                start: [77.1025, 28.7041],
                destination: [75.7873, 26.9124],
                geometry: [
                    [77.1025, 28.7041],
                    [76.98, 28.45],
                    [76.82, 28.21],
                    [76.60, 27.99],
                    [76.38, 27.65],
                    [76.10, 27.35],
                    [75.90, 27.10],
                    [75.7873, 26.9124]
                ],
                coordinates: [
                    [77.1025, 28.7041],
                    [76.98, 28.45],
                    [76.82, 28.21],
                    [76.60, 27.99],
                    [76.38, 27.65],
                    [76.10, 27.35],
                    [75.90, 27.10],
                    [75.7873, 26.9124]
                ],
                distanceKm: 237.5,
                mode: 'geodesic_measurement',
                steps: []
            };
            map.getState().setRoute(routeData);
            map.getState().dispatchCommand({
                type: 'set_route',
                route: routeData,
                fit: true
            });
            map.getState().setCamera({ longitude: 76.45, latitude: 27.81, zoom: 7.2 });
        """)
        wait_for_map_readiness(timeout_sec=15, require_route=True)
        # Regression: geodesic route MUST NOT render driving-navigation terms
        route_html = html.unescape(page.content())
        forbidden_geodesic = ["NH48", "bypass highway", "DRIVING"]
        for forbidden in forbidden_geodesic:
            if forbidden in route_html:
                raise AssertionError(
                    f"REGRESSION FAIL: geodesic_measurement route rendered forbidden driving term: '{forbidden}'"
                )
        assert_and_capture(
            "06_map_route.png",
            expected_type="map",
            expected_renderer="MapEngine + GeodesicCorridorOverlay",
            content_keys="route",
            required_selectors_or_texts=['Delhi Core', 'Jaipur Relay', 'Fit Corridor', 'GEODESIC', '237.5 km']
        )

        # ----------------------------------------------------
        # 6b. Map Close-up: Delhi/Jaipur Corridor Contrast
        # ----------------------------------------------------
        setup_state("""
            const { map } = window.__CHARLIE_STORES__;
            map.getState().dispatchCommand({
                type: 'fly_to',
                longitude: 76.4,
                latitude: 27.8,
                zoom: 8.2,
                durationMs: 400
            });
            map.getState().setCamera({ longitude: 76.4, latitude: 27.8, zoom: 8.2 });
        """)
        wait_for_map_readiness(timeout_sec=12)
        assert_and_capture(
            "04b_map_closeup.png",
            expected_type="map",
            expected_renderer="MapEngine (Corridor Close-up)",
            content_keys="route,camera",
            required_selectors_or_texts=['.maplibregl-canvas']
        )

        # ----------------------------------------------------
        # 7. Map - Intelligence Layer Enabled
        # ----------------------------------------------------
        setup_state("""
            const { map } = window.__CHARLIE_STORES__;
            map.getState().clearRoute();
            map.getState().setLayerEnabled('earthquakes', true);
            map.getState().setLayerData('earthquakes', [
                { id: 'eq1', label: 'M6.1 Honshu Seismic Event', category: 'Seismic', coordinates: [140.8, 36.8], severity: 'high' },
                { id: 'eq2', label: 'M4.8 Kanto Cluster', category: 'Seismic', coordinates: [139.6, 35.6], severity: 'medium' }
            ], { status: 'ready', attribution: 'USGS Hazards', lastUpdated: Date.now(), count: 2 });
            map.getState().dispatchCommand({
                type: 'fly_to',
                longitude: 140.2,
                latitude: 36.2,
                zoom: 6.8,
                durationMs: 400
            });
            map.getState().setCamera({ longitude: 140.2, latitude: 36.2, zoom: 6.8 });
        """)
        # Open Layer Controls dropdown to visually display layer items
        try:
            page.locator("button[aria-label='Toggle intelligence layers menu']").click()
        except Exception:
            pass
        wait_for_map_readiness(timeout_sec=12)
        assert_and_capture(
            "07_map_layer_enabled.png",
            expected_type="map",
            expected_renderer="MapEngine + LayerControls + Features",
            content_keys="activeLayers,layerData,layerMetadata",
            required_selectors_or_texts=['INTELLIGENCE LAYERS', 'Earthquakes (M2.5+)', '.maplibregl-canvas']
        )

        # Close Map Workspace
        setup_state("""
            const { workspace } = window.__CHARLIE_STORES__;
            workspace.getState().closeWorkspace('ws_map');
        """)

        # ----------------------------------------------------
        # 8. Research - Rich / Spatial Result
        # ----------------------------------------------------
        setup_state("""
            const { workspace, map } = window.__CHARLIE_STORES__;
            workspace.getState().openWorkspace({
                id: 'ws_research_spatial',
                kind: 'workspace',
                title: 'RESEARCH // PACIFIC FIBER TOPOLOGY',
                workspaceType: 'research',
                workspace_type: 'research',
                type: 'research',
                mode: 'workspace',
                target: 'workspace',
                summary: 'Analysis of high-bandwidth trans-Pacific corridors connecting Tokyo, Guam, and San Francisco.',
                content: {
                    title: 'PACIFIC FIBER TOPOLOGY & SEISMO-SPATIAL CORRELATION',
                    subtitle: 'Trans-Pacific subsea routes, landing clusters, and tectonic fault intersections',
                    summary: 'Synthesis of high-bandwidth trans-Pacific corridors connecting Tokyo, Guam, and San Francisco. High resilience observed across Northern segments.',
                    confidence: 0.94,
                    status: 'VERIFIED',
                    spatial_map: {
                        mode: 'geo',
                        center: [140.0, 35.0],
                        rangeKm: 2500,
                        objects: [
                            { id: 'hub_1', label: 'Tokyo Landing Station', angle: 45, distance: 0.3, type: 'hub' },
                            { id: 'hub_2', label: 'Guam Gateway', angle: 160, distance: 0.7, type: 'node' },
                            { id: 'cable_1', label: 'Trans-Pac Express', angle: 80, distance: 0.55, type: 'signal' }
                        ]
                    },
                    findings: [
                        { id: 'f1', title: 'Latency Optimization Corridor', detail: 'New low-loss optical route reduces Tokyo-SF roundtrip latency by 8.4ms.', iconType: 'trend', confidence: 0.96 },
                        { id: 'f2', title: 'Fault Line Redundancy', detail: 'Multi-landing mesh automatically reroutes traffic away from subsea seismic zones.', iconType: 'shield', confidence: 0.91 },
                        { id: 'f3', title: 'Bandwidth Saturation', detail: 'Peak nocturnal traffic loads sustained at 94.2% theoretical throughput with zero packet drop.', iconType: 'signal', confidence: 0.88 }
                    ],
                    sources: [
                        { id: 's1', title: 'Subsea Cable Telemetry Index', url: 'https://subsea.internal/telemetry', publisher: 'Pacific Ocean Comms' },
                        { id: 's2', title: 'Geological Hazard Assessment', url: 'https://geo.network/pacific', publisher: 'USGS Seismology' }
                    ]
                }
            });
            // Keep the acceptance fixture's Pacific geography aligned with the live MapEngine.
            // This is QA-only camera input; production map defaults remain payload-driven.
            map.getState().dispatchCommand({
                type: 'fly_to',
                longitude: 160.0,
                latitude: 30.0,
                zoom: 2.2,
                durationMs: 0
            });
        """)
        time.sleep(1.2)
        assert_and_capture(
            "08_research_spatial.png",
            expected_type="research",
            expected_renderer="ResearchWorkspace (Spatial Map)",
            content_keys="spatial_map,findings,sources,summary,confidence",
            required_selectors_or_texts=['PACIFIC FIBER TOPOLOGY', 'SYNTHESIS SUMMARY', 'SUPPORTING EVIDENCE', 'Latency Optimization Corridor']
        )

        # ----------------------------------------------------
        # 9. Research - Text / Non-Spatial Result
        # ----------------------------------------------------
        setup_state("""
            const { workspace } = window.__CHARLIE_STORES__;
            workspace.getState().closeWorkspace('ws_research_spatial');
            workspace.getState().openWorkspace({
                id: 'ws_research_text',
                kind: 'workspace',
                title: 'RESEARCH // DISTRIBUTED CONSENSUS',
                workspaceType: 'research',
                workspace_type: 'research',
                type: 'research',
                mode: 'workspace',
                target: 'workspace',
                summary: 'Evaluation of Raft vs Paxos derivatives under 5% packet loss and high network jitter.',
                content: {
                    title: 'DISTRIBUTED CONSENSUS IN DEGRADED ENVIRONMENTS',
                    subtitle: 'Comparative evaluation of Raft, Multi-Paxos, and Byzantine fault-tolerant state machine replication',
                    summary: 'Evaluation of Raft vs Paxos derivatives under 5% packet loss and high jitter indicates dynamic adaptive election timeouts prevent spurious re-elections by 87%.',
                    confidence: 0.98,
                    status: 'PEER-VERIFIED',
                    findings: [
                        { id: 'f1', title: 'Heartbeat Tuning', detail: 'Dynamic adaptive election timeouts prevent spurious leader re-elections during transit degradation.', confidence: 0.99 },
                        { id: 'f2', title: 'Log Compaction & Deltas', detail: 'Incremental delta snapshots reduce sync network footprint by 62% across slow satellite uplinks.', confidence: 0.96 },
                        { id: 'f3', title: 'Quorum Slicing Architecture', detail: 'Regional sub-quorums guarantee local read/write availability during inter-datacenter network partitions.', confidence: 0.93 }
                    ],
                    chart: {
                        chartType: 'line',
                        title: 'VERIFICATION COVERAGE',
                        unit: '%',
                        data: [
                            { label: 'Sources', value: 82 },
                            { label: 'Findings', value: 94 },
                            { label: 'Contradictions', value: 12 },
                            { label: 'Verified', value: 98 }
                        ]
                    },
                    timeline_items: [
                        { time: '09:10 UTC', title: 'Source corpus reconciled', status: 'completed' },
                        { time: '09:24 UTC', title: 'Finding confidence updated', status: 'active' }
                    ],
                    sources: [
                        { id: 's1', title: 'Consensus Protocols Survey 2026', url: 'https://arxiv.org/abs/sample1', publisher: 'Distributed Systems Journal' },
                        { id: 's2', title: 'State Machine Replication at Scale', url: 'https://dsj.internal/paper2', publisher: 'ACM Transactions' }
                    ]
                }
            });
        """)
        time.sleep(1.2)
        assert_and_capture(
            "09_research_non_spatial.png",
            expected_type="research",
            expected_renderer="ResearchWorkspace (Editorial)",
            content_keys="summary,findings,sources,confidence,status",
            required_selectors_or_texts=['PRIMARY RESEARCH SYNTHESIS', 'KEY FINDINGS & ANALYTICAL SIGNALS', 'Heartbeat Tuning', 'VERIFIED EVIDENCE & SOURCES']
        )

        # ----------------------------------------------------
        # 10. Briefing - Geographic / Map Briefing
        # ----------------------------------------------------
        setup_state("""
            const { workspace } = window.__CHARLIE_STORES__;
            workspace.getState().closeWorkspace('ws_research_text');
            workspace.getState().openWorkspace({
                id: 'ws_briefing_geo',
                kind: 'workspace',
                title: 'BRIEFING // GLOBAL SITUATION REPORT',
                workspaceType: 'briefing',
                workspace_type: 'briefing',
                type: 'briefing',
                mode: 'workspace',
                target: 'workspace',
                summary: 'Active monitoring of Northern Hemisphere atmospheric systems and regional infrastructure nodes.',
                content: {
                    headline: 'Active monitoring of Northern Hemisphere atmospheric systems and regional nodes.',
                    summaries: [
                        'Atmospheric depression over North Atlantic impacting transatlantic flight corridors with average delays of 42 minutes.',
                        'European regional grid frequency stabilized at 50.02 Hz following scheduled offshore wind synchronizations.'
                    ],
                    geo_data: {
                        mode: 'geo',
                        points: [
                            { id: 'p1', label: 'North Atlantic Storm Center', coordinates: [-30.0, 52.0], severity: 'high' },
                            { id: 'p2', label: 'London Air Traffic Hub', coordinates: [-0.12, 51.5], severity: 'normal' },
                            { id: 'p3', label: 'Frankfurt Core Exchange', coordinates: [8.68, 50.11], severity: 'normal' }
                        ]
                    },
                    timeline_items: [
                        { time: '06:00 UTC', title: 'Sensor calibration complete across all meteorological stations' },
                        { time: '09:30 UTC', title: 'Automated satellite weather feed correlation verified' },
                        { time: '11:15 UTC', title: 'Regional civil aviation advisory broadcast issued' }
                    ],
                    sources: [
                        { id: 'b1', title: 'Global Observation Network', publisher: 'WMO Telemetry', url: 'https://gon.internal' },
                        { id: 'b2', title: 'Eurocontrol Air Traffic Advisory', publisher: 'Eurocontrol', url: 'https://eurocontrol.int' }
                    ]
                }
            });
        """)
        time.sleep(1.2)
        assert_and_capture(
            "10_briefing_geographic.png",
            expected_type="briefing",
            expected_renderer="BriefingWorkspace (Geographic)",
            content_keys="headline,summaries,geo_data,timeline_items,sources",
            required_selectors_or_texts=['OPERATIONAL INTELLIGENCE BRIEFING', 'TOP HEADLINE', 'KEY TIMELINE', 'Active monitoring of Northern Hemisphere']
        )

        # ----------------------------------------------------
        # 11. Briefing - Non-Map Briefing
        # ----------------------------------------------------
        setup_state("""
            const { workspace } = window.__CHARLIE_STORES__;
            workspace.getState().closeWorkspace('ws_briefing_geo');
            workspace.getState().openWorkspace({
                id: 'ws_briefing_text',
                kind: 'workspace',
                title: 'DAILY EXECUTIVE BRIEFING',
                workspaceType: 'briefing',
                workspace_type: 'briefing',
                type: 'briefing',
                mode: 'workspace',
                target: 'workspace',
                summary: 'Autonomous OS infrastructure update, security policy verification, and performance summary.',
                content: {
                    headline: 'Autonomous OS infrastructure update, security posture, and runtime health.',
                    summaries: [
                        'All 8 core subsystem health probes reporting optimal operational parameters with zero memory leaks.',
                        'Audit logs confirmed zero unverified external API calls during 24-hour continuous autonomous execution window.',
                        'Scheduled database maintenance and vector index compaction completed in 1.4 seconds with zero downtime.'
                    ],
                    timeline_items: [
                        { time: '00:00 UTC', title: 'Memory consolidation and graph vector indexing completed' },
                        { time: '04:15 UTC', title: 'Routine cache rotation and cryptographically signed log archiving' },
                        { time: '08:00 UTC', title: 'Daily health diagnostics verified across all background daemons' }
                    ],
                    sources: [
                        { id: 's1', title: 'Charlie Internal Health Journal', publisher: 'Host OS Diagnostic Daemon', url: 'https://health.local' }
                    ]
                }
            });
        """)
        time.sleep(1.2)
        assert_and_capture(
            "11_briefing_non_map.png",
            expected_type="briefing",
            expected_renderer="BriefingWorkspace (Editorial)",
            content_keys="headline,summaries,timeline_items,sources",
            required_selectors_or_texts=['OPERATIONAL INTELLIGENCE BRIEFING', 'TOP HEADLINE', 'KEY TIMELINE', 'Autonomous OS infrastructure update']
        )

        # ----------------------------------------------------
        # 12. System Workspace
        # ----------------------------------------------------
        setup_state("""
            const { workspace } = window.__CHARLIE_STORES__;
            workspace.getState().closeWorkspace('ws_briefing_text');
            workspace.getState().openWorkspace({
                id: 'ws_system',
                kind: 'workspace',
                title: 'SYSTEM DIAGNOSTICS // HOST RUNTIME',
                workspaceType: 'system',
                workspace_type: 'system',
                type: 'system',
                mode: 'workspace',
                target: 'workspace',
                summary: 'Active system telemetry, host vital signs, active process mesh, and diagnostic logging.',
                content: {
                    title: 'MACHINE DIAGNOSTICS & SYSTEM TELEMETRY',
                    subtitle: 'ACTIVE RUNTIME // HOST HEALTH // PROCESS MESH',
                    vitals: {
                        title: 'SYSTEM STATUS',
                        gauges: [
                            { id: 'cpu', label: 'CPU LOAD', value: 24 },
                            { id: 'ram', label: 'MEMORY', value: 48 },
                            { id: 'vram', label: 'GPU VRAM', value: 36 }
                        ],
                        stats: [
                            { label: 'CPU TEMP', value: '44°C' },
                            { label: 'FAN SPEED', value: '1850 RPM' },
                            { label: 'UPTIME', value: '48h 12m' }
                        ]
                    },
                    operations: [
                        { id: 'op1', title: 'SPATIAL TILE CACHE INDEXING', subtitle: 'Verifying local PMTiles directory integrity', progress: 85, status: 'RUNNING' },
                        { id: 'op2', title: 'VECTOR GRAPH EMBEDDING', subtitle: 'Incremental knowledge graph compaction', progress: 40, status: 'RUNNING' },
                        { id: 'op3', title: 'DIAGNOSTIC BACKUP SNAPSHOT', subtitle: 'Scheduled disk snapshot retention', progress: 0, status: 'QUEUED' }
                    ],
                    topology: {
                        mode: 'topology',
                        nodes: [
                            { id: 'brain', label: 'Charlie Core', sublabel: 'available', x: 50, y: 50, status: 'active' },
                            { id: 'voice', label: 'Voice', sublabel: 'active', x: 20, y: 25, status: 'active' },
                            { id: 'research', label: 'Research', sublabel: 'active', x: 80, y: 25, status: 'active' },
                            { id: 'browser', label: 'Browser', sublabel: 'available', x: 18, y: 75, status: 'idle' },
                            { id: 'memory', label: 'Memory', sublabel: 'degraded', x: 82, y: 75, status: 'warning' },
                            { id: 'mcp', label: 'MCP', sublabel: 'unknown', x: 50, y: 15, status: 'idle' }
                        ],
                        edges: [
                            { from: 'brain', to: 'voice', type: 'link', active: true },
                            { from: 'brain', to: 'research', type: 'link', active: true },
                            { from: 'brain', to: 'browser', type: 'link', active: true },
                            { from: 'brain', to: 'memory', type: 'link', active: false },
                            { from: 'brain', to: 'mcp', type: 'link', active: false }
                        ]
                    },
                    processes: {
                        title: 'WHAT IS RUNNING',
                        processes: [
                            { name: 'charlie-brain', pid: 1402, status: 'RUNNING', uptime: '48h' },
                            { name: 'voice-asr-worker', pid: 1409, status: 'IDLE', uptime: '48h' },
                            { name: 'geo-tile-service', pid: 1412, status: 'RUNNING', uptime: '12h' },
                            { name: 'mcp-host-bridge', pid: 1420, status: 'RUNNING', uptime: '48h' }
                        ]
                    },
                    logs: [
                        { timestamp: '00:35:12', level: 'INFO', message: 'Tile service verified PMTiles directory containment.' },
                        { timestamp: '00:35:18', level: 'INFO', message: 'Nominal HTTP status 200 returned from Nominatim cache provider.' },
                        { timestamp: '00:35:24', level: 'INFO', message: 'Zustand state projection synced with backend presentation intent.' }
                    ]
                }
            });
        """)
        time.sleep(1.2)
        assert_and_capture(
            "12_system_workspace.png",
            expected_type="system",
            expected_renderer="SystemWorkspace",
            content_keys="vitals,operations,processes,logs",
            required_selectors_or_texts=['MACHINE DIAGNOSTICS & SYSTEM TELEMETRY', 'ACTIVE SYSTEM OPERATIONS', 'SPATIAL TILE CACHE INDEXING', 'DIAGNOSTIC LOG STREAM']
        )

        # ----------------------------------------------------
        # 13. CPU / Small System Temporary Widget
        # ----------------------------------------------------
        setup_state("""
            const { workspace, widget } = window.__CHARLIE_STORES__;
            workspace.getState().closeWorkspace('ws_system');
            widget.getState().upsertWidget({
                id: 'widget_cpu',
                kind: 'widget',
                widgetType: 'system',
                widget_type: 'system',
                type: 'system',
                title: 'CPU USAGE',
                summary: 'Core Load: 18% | 8 Cores Active',
                autoDismissMs: 5000,
                content: {
                    metric_name: 'CPU USAGE',
                    value: 18,
                    temperature: '42°C',
                    fan_speed: '1800 RPM',
                    history: [12, 14, 18, 16, 22, 20, 18]
                }
            }, {
                viewport: { width: 1920, height: 1080 },
                safeMargin: { x: 32, y: 32 },
                coreBounds: { x: 960-150, y: 540-150, width: 300, height: 300 },
                workspaceBounds: null
            });
        """)
        time.sleep(1.2)
        assert_and_capture(
            "13_cpu_small_widget.png",
            expected_type="system_widget",
            expected_renderer="WidgetContainer -> SystemWidget",
            content_keys="metric_name,value,temperature,history",
            required_selectors_or_texts=['CPU USAGE', '18%']
        )

        # ----------------------------------------------------
        # 14. Tasks Workspace
        # ----------------------------------------------------
        setup_state("""
            const { widget, workspace, charlie } = window.__CHARLIE_STORES__;
            widget.getState().dismissWidget('widget_cpu');
            charlie.setState({
                tasks: {
                    'task_01': {
                        id: 'task_01',
                        title: 'Spatial Intelligence & Regional Analysis',
                        status: 'running',
                        currentStep: 3,
                        totalSteps: 5,
                        progress: 0.6,
                        origin: 'foreground',
                        priority: 'high',
                        currentAction: 'Correlating regional intelligence sources',
                        capabilityRequirements: ['ResearchCapability', 'MapCapability'],
                        approvalReference: 'not_required'
                    },
                    'task_02': {
                        id: 'task_02',
                        title: 'Local Vector Graph Indexing',
                        status: 'running',
                        currentStep: 2,
                        totalSteps: 4,
                        progress: 0.5,
                        origin: 'background',
                        priority: 'normal',
                        currentAction: 'Compacting local graph segments',
                        capabilityRequirements: ['MemoryCapability'],
                        resultReference: 'task://graph-indexing'
                    }
                }
            });
            workspace.getState().openWorkspace({
                id: 'ws_tasks',
                kind: 'workspace',
                title: 'ACTIVE TASKS // CONCURRENCY JOURNAL',
                workspaceType: 'tasks',
                workspace_type: 'tasks',
                type: 'tasks',
                mode: 'workspace',
                target: 'workspace',
                taskId: 'task_01',
                summary: 'Real-time task execution plan, progress steps, and concurrent task journal.',
                content: {
                    title: 'Spatial Intelligence & Regional Analysis'
                }
            });
        """)
        time.sleep(1.2)
        assert_and_capture(
            "14_tasks_workspace.png",
            expected_type="tasks",
            expected_renderer="TasksWorkspace",
            content_keys="tasks,currentTask,steps",
            required_selectors_or_texts=['TASK EXECUTION WORKSPACE', 'EXECUTION PLAN & STATUS', 'CONCURRENT TASKS']
        )

        # ----------------------------------------------------
        # 15. Settings Modal
        # ----------------------------------------------------
        setup_state("""
            const { workspace } = window.__CHARLIE_STORES__;
            workspace.getState().closeWorkspace('ws_tasks');
            if (window.__OPEN_SETTINGS__) window.__OPEN_SETTINGS__();
        """)
        time.sleep(1.2)
        assert_and_capture(
            "15_settings_modal.png",
            expected_type="settings",
            expected_renderer="SettingsModal -> Settings",
            content_keys="categories,fields,models",
            required_selectors_or_texts=['CONFIGURATION & SYSTEM SETTINGS', 'CLOSE']
        )

        # ----------------------------------------------------
        # 16. Docked Charlie with Workspace Open
        # ----------------------------------------------------
        setup_state("""
            if (window.__CLOSE_SETTINGS__) window.__CLOSE_SETTINGS__();
            const { workspace } = window.__CHARLIE_STORES__;
            workspace.getState().openWorkspace({
                id: 'ws_docked_check',
                kind: 'workspace',
                title: 'RESEARCH // QUANTUM COMPUTING',
                workspaceType: 'research',
                workspace_type: 'research',
                type: 'research',
                mode: 'workspace',
                target: 'workspace',
                summary: 'Validation of clean docked bottom-right Charlie core without redundant labels.',
                content: {
                    title: 'QUANTUM ERROR CORRECTION DYNAMICS',
                    summary: 'Topological surface codes demonstrate fault-tolerance threshold under physical error rate of 0.7%.'
                }
            });
        """)
        time.sleep(1.2)
        assert_and_capture(
            "16_docked_charlie_clean.png",
            expected_type="research",
            expected_renderer="ResearchWorkspace + Docked CharlieCore",
            content_keys="summary,title",
            required_selectors_or_texts=['.charlie-core-docked', 'QUANTUM ERROR CORRECTION DYNAMICS']
        )

        # ----------------------------------------------------
        # 17. Responsive Viewport: 1366x768
        # ----------------------------------------------------
        page.set_viewport_size({"width": 1366, "height": 768})
        time.sleep(1.0)
        assert_and_capture(
            "responsive_1366x768.png",
            expected_type="research",
            expected_renderer="ResearchWorkspace (1366x768)",
            content_keys="responsive_viewport",
            required_selectors_or_texts=['QUANTUM ERROR CORRECTION DYNAMICS']
        )

        # ----------------------------------------------------
        # 18. Responsive Viewport: 1024x768
        # ----------------------------------------------------
        page.set_viewport_size({"width": 1024, "height": 768})
        time.sleep(1.0)
        assert_and_capture(
            "responsive_1024x768.png",
            expected_type="research",
            expected_renderer="ResearchWorkspace (1024x768)",
            content_keys="responsive_viewport",
            required_selectors_or_texts=['QUANTUM ERROR CORRECTION DYNAMICS']
        )

        browser.close()
        print("\n=======================================================")
        print("ALL 18 PHASE 9 QA SCREENSHOTS CAPTURED & VERIFIED PASS!")
        print("=======================================================")

if __name__ == "__main__":
    try:
        run_qa_captures()
    except Exception as e:
        print(f"\n[QA ERROR] Assertion failed: {e}", file=sys.stderr)
        sys.exit(1)
