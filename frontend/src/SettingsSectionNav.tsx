export type SettingsSectionId = "presence" | "voice" | "runtime" | "privacy";

const sections: Array<{ id: SettingsSectionId; label: string }> = [
  { id: "presence", label: "Presence" },
  { id: "voice", label: "Voice" },
  { id: "runtime", label: "Runtime" },
  { id: "privacy", label: "Privacy" },
];

export function SettingsSectionNav({
  active,
  onSelect,
}: {
  active: SettingsSectionId;
  onSelect: (section: SettingsSectionId) => void;
}) {
  return (
    <nav className="settings-nav" aria-label="Settings sections">
      {sections.map((section) => (
        <button
          type="button"
          key={section.id}
          data-section={section.id}
          aria-pressed={active === section.id}
          onClick={() => onSelect(section.id)}
        >
          {section.label}
        </button>
      ))}
    </nav>
  );
}
