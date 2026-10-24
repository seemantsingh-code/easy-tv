"""Build a public, unsigned Apple Shortcuts template. Never reads local settings."""
from pathlib import Path
import plistlib
import uuid


def build():
    ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, 'https://example.org/easy-tv/' + str(i))).upper() for i in range(6)]
    def output(index, label):
        return {'Type': 'ActionOutput', 'OutputUUID': ids[index], 'OutputName': label}
    def text(value, index=None, label='Text'):
        data = {'string': value}
        if index is not None:
            data['attachmentsByRange'] = {'{%d, 1}' % value.index('\ufffc'): output(index, label)}
        return {'Value': data, 'WFSerializationType': 'WFTextTokenString'}
    def dictionary(values):
        return {'Value': {'WFDictionaryFieldValueItems': [
            {'WFItemType': 0, 'WFKey': text(key), 'WFValue': value} for key, value in values.items()]},
            'WFSerializationType': 'WFDictionaryFieldValue'}
    def action(name, parameters, index):
        return {'WFWorkflowActionIdentifier': 'is.workflow.actions.' + name,
                'WFWorkflowActionParameters': dict(parameters, UUID=ids[index])}
    actions = [
        action('gettext', {'WFTextActionText': 'http://easy-tv.local:8767/command'}, 0),
        action('gettext', {'WFTextActionText': 'SET_YOUR_BRIDGE_TOKEN'}, 1),
        action('ask', {'WFAskActionPrompt': 'What should the TV do?', 'WFInputType': 'Text'}, 2),
        action('downloadurl', {'WFURL': text('\ufffc', 0), 'WFHTTPMethod': 'POST', 'WFHTTPBodyType': 'JSON',
                              'WFJSONValues': dictionary({'command': text('\ufffc', 2, 'Provided Input')}),
                              'WFHTTPHeaders': dictionary({'Authorization': text('Bearer \ufffc', 1)})}, 3),
        action('getvalueforkey', {'WFDictionaryKey': 'message', 'WFGetDictionaryValueType': 'Value',
                                  'WFInput': {'Value': output(3, 'Contents of URL'), 'WFSerializationType': 'WFTextTokenAttachment'}}, 4),
        action('showresult', {'Text': text('\ufffc', 4, 'Dictionary Value')}, 5)]
    return {'WFWorkflowName': 'easy-tv', 'WFWorkflowClientVersion': '2600',
            'WFWorkflowMinimumClientVersion': 900, 'WFWorkflowMinimumClientVersionString': '900',
            'WFWorkflowIcon': {'WFWorkflowIconStartColor': 4282601983, 'WFWorkflowIconGlyphNumber': 59511},
            'WFWorkflowTypes': [], 'WFWorkflowInputContentItemClasses': [],
            'WFQuickActionSurfaces': [], 'WFWorkflowActions': actions,
            'WFWorkflowImportQuestions': [
                {'ActionIndex': 0, 'ParameterKey': 'WFTextActionText', 'Category': 'Parameter',
                 'Text': 'Full URL to YOUR bridge, ending in /command', 'DefaultValue': 'http://easy-tv.local:8767/command'},
                {'ActionIndex': 1, 'ParameterKey': 'WFTextActionText', 'Category': 'Parameter',
                 'Text': 'Your private bridge token (easy-tv token)', 'DefaultValue': 'SET_YOUR_BRIDGE_TOKEN'}]}


if __name__ == '__main__':
    target = Path(__file__).resolve().parents[1] / 'src/easy_tv/resources/shortcuts/easy-tv.unsigned.shortcut'
    target.write_bytes(plistlib.dumps(build(), sort_keys=False))
