/* slider.js — Range slider + value display binding */

export function bindSlider(sliderId, displayId, onChange) {
    const slider = document.getElementById(sliderId);
    const display = document.getElementById(displayId);
    if (!slider || !display) return;

    const update = () => {
        display.textContent = slider.value;
        if (onChange) onChange(parseFloat(slider.value));
    };

    slider.addEventListener('input', update);
    update(); // sync initial state
    return { slider, display, update };
}

export function setSlider(sliderId, displayId, value) {
    const slider = document.getElementById(sliderId);
    const display = document.getElementById(displayId);
    if (slider) slider.value = value;
    if (display) display.textContent = value;
}
