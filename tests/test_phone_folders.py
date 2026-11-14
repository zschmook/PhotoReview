import tempfile
import unittest
from pathlib import Path
from photo_review.phones import PortableDevice,same_physical_phone
from photo_review.phone_folders import phone_destination,remember_phone_destination,validate_phone_destination


class PhoneFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'library';self.root.mkdir()
        self.first=PortableDevice('USB\\VID_18D1&PID_4EE1\\SERIAL-A','Pixel 8')
        self.second=PortableDevice('USB\\VID_18D1&PID_4EE1\\SERIAL-B','Pixel 8')

    def test_same_model_phones_have_distinct_remembered_destinations(self):
        first=phone_destination(self.first,self.root,{})
        settings=remember_phone_destination({},self.first,self.root,first,'day-tree')
        second=phone_destination(self.second,self.root,settings)
        self.assertEqual(first,self.root/'Pixel 8');self.assertNotEqual(first,second)
        self.assertTrue(second.name.startswith('Pixel 8 - '));self.assertFalse(first.exists());self.assertFalse(second.exists())
        settings=remember_phone_destination(settings,self.second,self.root,second,'month-tree')
        self.assertEqual(phone_destination(self.first,self.root,settings),first)
        self.assertEqual(phone_destination(self.second,self.root,settings),second)

    def test_custom_folder_survives_name_change_and_device_id_casing(self):
        custom=self.root/'Family'/'Aunts phone'
        settings=remember_phone_destination({},self.first,self.root,custom,'day-folder')
        renamed=PortableDevice(self.first.identity.lower(),'Renamed Pixel')
        self.assertEqual(phone_destination(renamed,self.root,settings),custom)
        self.assertEqual(next(iter(next(iter(settings.values()))['folders'].values())),'Family/Aunts phone')

    def test_each_library_has_independent_phone_folder_choice(self):
        other=Path(self.temp.name)/'other-library';other.mkdir()
        one=self.root/'First custom';two=other/'Second custom'
        settings=remember_phone_destination({},self.first,self.root,one,'day-tree')
        self.assertEqual(phone_destination(self.first,other,settings),other/'Pixel 8')
        settings=remember_phone_destination(settings,self.first,other,two,'month-tree')
        self.assertEqual(phone_destination(self.first,self.root,settings),one)
        self.assertEqual(phone_destination(self.first,other,settings),two)

    def test_existing_model_folder_does_not_trigger_an_id_suffix(self):
        existing=self.root/'Pixel 8';existing.mkdir();photo=existing/'existing.jpg';photo.write_bytes(b'untouched')
        destination=phone_destination(self.first,self.root,{})
        self.assertEqual(destination,existing);self.assertEqual(photo.read_bytes(),b'untouched')

    def test_different_models_keep_plain_names_even_with_existing_folders(self):
        first=self.root/'Pixel 8';first.mkdir()
        settings=remember_phone_destination({},self.first,self.root,first,'day-tree')
        second=PortableDevice('different-phone-id','Galaxy S24')
        existing=self.root/'Galaxy S24';existing.mkdir()
        self.assertEqual(phone_destination(second,self.root,settings),existing)
        settings=remember_phone_destination(settings,second,self.root,existing,'month-tree')
        self.assertEqual(phone_destination(self.first,self.root,settings),first)
        self.assertEqual(phone_destination(second,self.root,settings),existing)

    def test_second_same_model_gets_id_even_when_first_uses_custom_folder(self):
        settings=remember_phone_destination({},self.first,self.root,self.root/'Aunts phone','day-tree')
        self.assertTrue(phone_destination(self.second,self.root,settings).name.startswith('Pixel 8 - '))

    def test_different_model_custom_folder_collision_uses_number_instead_of_id(self):
        settings=remember_phone_destination({},self.first,self.root,self.root/'Galaxy S24','day-tree')
        second=PortableDevice('another-device-id','Galaxy S24')
        self.assertEqual(phone_destination(second,self.root,settings),self.root/'Galaxy S24 - 2')

    def test_legacy_known_phone_preserves_its_model_folder(self):
        existing=self.root/'Pixel 8';existing.mkdir()
        settings={self.first.identity:{'layout':'month-tree','destination':str(Path(self.temp.name)/'old-outside-folder')}}
        self.assertEqual(phone_destination(self.first,self.root,settings),existing)
        settings=remember_phone_destination(settings,self.first,self.root,existing,'month-tree')
        self.assertNotIn('destination',settings[self.first.identity.casefold()])
        self.assertNotEqual(phone_destination(self.second,self.root,settings),existing)

    def test_other_phone_cannot_reuse_owned_folder_even_before_it_exists(self):
        chosen=self.root/'Family photos'
        settings=remember_phone_destination({},self.first,self.root,chosen,'day-tree')
        with self.assertRaisesRegex(ValueError,'another phone'):
            remember_phone_destination(settings,self.second,self.root,chosen,'month-tree')
        with self.assertRaisesRegex(ValueError,'another phone'):
            validate_phone_destination(self.second,self.root,chosen,settings)
        self.assertFalse(chosen.exists())

    def test_stale_saved_destination_cannot_escape_current_library(self):
        settings=remember_phone_destination({},self.first,self.root,self.root/'Original','day-tree')
        record=settings[self.first.identity.casefold()]
        root_key=next(iter(record['folders']))
        for invalid in ('../outside',str(Path(self.temp.name)/'outside')):
            with self.subTest(invalid=invalid):
                record['folders'][root_key]=invalid
                self.assertTrue(phone_destination(self.first,self.root,settings).is_relative_to(self.root))

    def test_usb_mode_change_matches_serial_but_identical_model_does_not(self):
        mode=PortableDevice('USB\\VID_18D1&PID_4EE5&MI_00\\SERIAL-A','Pixel 8')
        self.assertTrue(same_physical_phone(self.first,mode))
        self.assertFalse(same_physical_phone(self.first,self.second))
        self.assertFalse(same_physical_phone(PortableDevice('USB\\VID_18D1&PID_4EE1\\6&PORT&0','Pixel 8'),PortableDevice('USB\\VID_18D1&PID_4EE5\\6&PORT&0','Pixel 8')))
