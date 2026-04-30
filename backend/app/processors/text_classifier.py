import re

class TextClassifier:
    THEMES = {
        'saúde': [r'saúde', r'hospital', r'médico', r'ubs', r'upa', r'vacina', r'remédio', r'sanitário'],
        'educação': [r'educação', r'escola', r'ensino', r'creche', r'professor', r'merenda', r'aluno'],
        'segurança': [r'segurança', r'guarda municipal', r'polícia', r'violência', r'crime', r'monitoramento'],
        'transporte': [r'transporte', r'ônibus', r'trânsito', r'mobilidade', r'tarifa', r'circulação'],
        'obras': [r'obras', r'pavimentação', r'asfalto', r'reforma', r'construção', r'infraestrutura'],
        'meio ambiente': [r'meio ambiente', r'árvore', r'parque', r'poluição', r'sustentável', r'ecologia'],
        'assistência social': [r'assistência social', r'vulnerável', r'cras', r'creas', r'social'],
        'finanças': [r'finanças', r'orçamento', r'imposto', r'iptu', r'iss', r'tesouro', r'contas'],
        'licitações': [r'licitação', r'pregão', r'edital', r'concorrência', r'tomada de preço'],
        'funcionalismo': [r'servidor', r'concurso', r'rh', r'pessoal', r'cargo', r'vencimento']
    }

    def classify(self, text):
        if not text:
            return 'outros'
            
        text = text.lower()
        scores = {theme: 0 for theme in self.THEMES}
        
        for theme, keywords in self.THEMES.items():
            for kw in keywords:
                if re.search(kw, text):
                    scores[theme] += 1
        
        # Retorna o tema com maior pontuação, ou 'outros' se nenhum atingir 0
        sorted_themes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        if sorted_themes[0][1] > 0:
            return sorted_themes[0][0]
        
        return 'outros'

if __name__ == "__main__":
    classifier = TextClassifier()
    print(classifier.classify("O hospital municipal recebeu novos equipamentos de saúde."))
    print(classifier.classify("A pavimentação da rua foi concluída pela secretaria de obras."))
