from abc import ABC, abstractmethod
import json
import re
from bs4 import Tag

# DART 공시 XML은 태그가 대문자(<TR>, <TD>)로 오는 경우가 많고,
# lxml-xml 파서는 태그 대소문자를 그대로 보존하므로 대소문자 무관 매칭이 필요함.
TR_PATTERN = re.compile(r'^tr$', re.I)
CELL_PATTERN = re.compile(r'^(td|th)$', re.I)


class BaseConverter(ABC):

    @abstractmethod
    def convertTab(self, table_tag: Tag) -> str:
        pass


class JsonConverter(BaseConverter):

    def convertTab(self, table_tag: Tag) -> str:
        """BS4 Table 태그를 읽어 JSON 문자열로 변환"""
        rows = table_tag.find_all(TR_PATTERN)
        if not rows:
            return json.dumps([])

        table_data = []
        header = []

        for i, row in enumerate(rows):
            cols = [
                ele.text.strip().replace('\n', ' ')
                for ele in row.find_all(CELL_PATTERN)
            ]
            if not cols or not any(cols):
                continue

            if i == 0 or not header:
                header = cols
            else:
                row_dict = {
                    header[j]
                    if j < len(header)
                    else f'col_{j}': cols[j]
                    for j in range(len(cols))
                }
                table_data.append(row_dict)

        return json.dumps(table_data, ensure_ascii=False)


class MarkdownConverter(BaseConverter):

    def convertTab(self, table_tag: Tag) -> str:
        """BS4 Table 태그를 읽어 Markdown 표 문자열로 변환"""
        rows = table_tag.find_all(TR_PATTERN)
        if not rows:
            return ''

        md_lines = []
        for i, row in enumerate(rows):
            cols = [
                ele.text.strip().replace('\n', ' ')
                for ele in row.find_all(CELL_PATTERN)
            ]
            if not cols or not any(cols):
                continue

            md_lines.append('| ' + ' | '.join(cols) + ' |')
            if i == 0:
                md_lines.append('| ' + ' | '.join(['---'] * len(cols)) + ' |')

        return '\n'.join(md_lines)


class ConverterFactory:

    _converters = {
        'md': MarkdownConverter,
        'json': JsonConverter,
    }

    @classmethod
    def get_converter(cls, tab_type: str) -> BaseConverter:
        converter_cls = cls._converters.get(tab_type.lower())
        if not converter_cls:
            raise ValueError(f'지원하지 않는 표 포맷: {tab_type}')
        return converter_cls()


def reconstruct_table(table_tag: Tag, tab_type: str) -> str:
    converter = ConverterFactory.get_converter(tab_type)
    return converter.convertTab(table_tag)
