(function () {
    'use strict';

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function renderRichText(value) {
        const source = String(value == null ? '' : value);
        const tokenPattern = /<math>([\s\S]*?)<\/math>|\\\(([\s\S]*?)\\\)|\\\[([\s\S]*?)\\\]/gi;
        let output = '';
        let cursor = 0;
        let match;

        while ((match = tokenPattern.exec(source)) !== null) {
            output += escapeHtml(source.slice(cursor, match.index)).replace(/\n/g, '<br>');
            const equation = match[1] || match[2] || match[3] || '';
            const display = Boolean(match[3]);
            output += display
                ? `<div class="pc-math-display">\\[${escapeHtml(equation)}\\]</div>`
                : `<span class="pc-math-inline">\\(${escapeHtml(equation)}\\)</span>`;
            cursor = match.index + match[0].length;
        }

        output += escapeHtml(source.slice(cursor)).replace(/\n/g, '<br>');
        return output;
    }

    function setRichText(element, value) {
        if (!element) return;
        element.innerHTML = renderRichText(value);
    }

    function normalizeContent(question) {
        const source = question || {};
        const content = source.content && typeof source.content === 'object' ? source.content : {};
        const contentSolution = content.solution && typeof content.solution === 'object' ? content.solution : {};
        const solutionContent = source.solution_content && typeof source.solution_content === 'object'
            ? source.solution_content
            : {};
        const options = Array.isArray(content.options)
            ? content.options
            : [source.option_a, source.option_b, source.option_c, source.option_d];

        return {
            question: content.question || source.question || source.question_text || '',
            options: options.slice(0, 4).map((value) => value || ''),
            solution: {
                answer: solutionContent.answer || contentSolution.answer || source.answer || '',
                steps: Array.isArray(solutionContent.steps)
                    ? solutionContent.steps
                    : (Array.isArray(contentSolution.steps) ? contentSolution.steps : []),
                equations: Array.isArray(solutionContent.equations)
                    ? solutionContent.equations
                    : (Array.isArray(contentSolution.equations) ? contentSolution.equations : []),
                explanation: solutionContent.explanation || contentSolution.explanation || source.solution || ''
            }
        };
    }

    function setQuestion(questionElement, optionElements, question) {
        const normalized = normalizeContent(question);
        setRichText(questionElement, normalized.question);
        normalized.options.forEach((option, index) => setRichText(optionElements[index], option));
        return normalized;
    }

    function renderSolution(container, solutionOrQuestion) {
        if (!container) return;
        const normalized = solutionOrQuestion && solutionOrQuestion.solution
            ? normalizeContent(solutionOrQuestion).solution
            : normalizeContent({ solution_content: solutionOrQuestion }).solution;

        container.innerHTML = '';

        if (normalized.answer) {
            const answer = document.createElement('div');
            answer.className = 'structured-answer';
            answer.innerHTML = `<strong>Answer</strong><div class="structured-answer-text">${renderRichText(normalized.answer)}</div>`;
            container.appendChild(answer);
        }

        if (normalized.steps.length) {
            const title = document.createElement('strong');
            title.className = 'structured-section-title';
            title.textContent = 'Step-by-step solution';
            container.appendChild(title);

            const list = document.createElement('ol');
            list.className = 'structured-steps';
            normalized.steps.forEach((step) => {
                const item = document.createElement('li');
                item.innerHTML = renderRichText(step);
                list.appendChild(item);
            });
            container.appendChild(list);
        }

        if (normalized.equations.length) {
            const title = document.createElement('strong');
            title.className = 'structured-section-title';
            title.textContent = 'Formulae';
            container.appendChild(title);

            const equations = document.createElement('div');
            equations.className = 'structured-equations';
            normalized.equations.forEach((equation) => {
                const item = document.createElement('div');
                item.className = 'structured-equation';
                item.innerHTML = `\\[${escapeHtml(equation)}\\]`;
                equations.appendChild(item);
            });
            container.appendChild(equations);
        }

        if (normalized.explanation) {
            const title = document.createElement('strong');
            title.className = 'structured-section-title';
            title.textContent = 'Explanation';
            container.appendChild(title);

            const explanation = document.createElement('div');
            explanation.className = 'structured-explanation';
            explanation.innerHTML = renderRichText(normalized.explanation);
            container.appendChild(explanation);
        }

        if (!container.children.length) {
            const empty = document.createElement('p');
            empty.className = 'structured-empty';
            empty.textContent = 'No detailed solution is available for this question yet.';
            container.appendChild(empty);
        }

        typeset(container);
    }

    function typeset(root) {
        if (window.MathJax && typeof window.MathJax.typesetPromise === 'function') {
            window.MathJax.typesetPromise(root ? [root] : undefined).catch(() => {});
        }
    }

    window.PrepCampusQuestionRenderer = {
        escapeHtml,
        renderRichText,
        setRichText,
        normalizeContent,
        setQuestion,
        renderSolution,
        typeset
    };
})();
