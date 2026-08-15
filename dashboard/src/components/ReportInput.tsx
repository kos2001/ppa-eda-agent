import { useLang } from "../i18n";

interface ReportInputProps {
  value: string;
  onChange: (value: string) => void;
  onLoadExample: () => void;
  placeholder: string;
}

export default function ReportInput({
  value,
  onChange,
  onLoadExample,
  placeholder,
}: ReportInputProps) {
  const { t } = useLang();
  return (
    <div className="report-input">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={10}
      />
      <div className="report-input__actions">
        <button onClick={onLoadExample}>{t("load_example")}</button>
      </div>
    </div>
  );
}
