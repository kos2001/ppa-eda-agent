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
  return (
    <div className="report-input">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={10}
      />
      <button onClick={onLoadExample}>Load example</button>
    </div>
  );
}
