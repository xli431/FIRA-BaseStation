export default function ParameterInput({ name, value, onUpdate }) {
    return (
        <div>
            <label htmlFor={`${name}-input`}>{name}:</label>
            <input
                type="number"
                id={`${name}-input`}
                step="0.01"
                value={value}
                onChange={(e) => onUpdate(name, e.target.value)}
            />
        </div>
    );
}