export default function TabButton({ children, isActive, onClick }) {
    const activeClass = 'bg-highlight-color text-bg-color border-highlight-color';
    const inactiveClass = 'bg-transparent text-text-color border-border-color';
    return (
        <button
            className={`tab-button ${isActive ? activeClass : inactiveClass}`}
            onClick={onClick}
        >
            {children}
        </button>
    );
}