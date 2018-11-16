from os import path, makedirs
import time

import tensorflow as tf

from model.imglinreg import ImgLinReg
from model.imglinregcplx import ImgLinRegCplx
from env import FLAGS, IN_SHAPE, PH_IN_SHAPE, PH_OUT_INDEX, SESS_CFG
from toolkit.dirty import np_mean, resolve_coord_out, latlong_distance
from toolkit.dataprep import Dataset_PRegv3 as Dataset


def main():

    sess = tf.Session(config=SESS_CFG)

    summaries_dir = path.abspath(FLAGS.summaries_dir)
    fold_dir = path.abspath(FLAGS.path_fold)
    ckpt_dir = path.abspath(FLAGS.ckpt_dir)
    if not path.exists(ckpt_dir):
        print('[E] Cannot find ckpt_dir')
        makedirs(ckpt_dir)
    else:
        pass

    min_list = [2, -12, 50, 850]
    max_list = [9, 68, 206, 1040]

    dataset = Dataset(path_scene=FLAGS.path_scene, path_track=FLAGS.path_track,
                      path_track_exception=FLAGS.path_track_exception,
                      path_fold=fold_dir, preset_fold=FLAGS.preset_fold, use_valid=FLAGS.use_valid,
                      in_shape=IN_SHAPE, batch_size=FLAGS.batch_size, concurrent=1, output_size=1,
                      norm_policy='minmax', min_list=min_list, max_list=max_list, input_norm=True, input_norm_scale=10)

    X = tf.placeholder(dtype=tf.float32, shape=PH_IN_SHAPE)
    Y = tf.placeholder(dtype=tf.float32, shape=PH_OUT_INDEX)

    if FLAGS.network_type == 'simple':
        Network = ImgLinReg
    elif FLAGS.network_type == 'cplx':
        Network = ImgLinRegCplx
    else:
        Network = None

    network = Network(x=X, y=Y,
                      conv_act_policy=FLAGS.conv_act_policy, flat_act_policy=FLAGS.flat_act_policy,
                      batch_norm=FLAGS.batch_norm, optimizer=getattr(tf.train, FLAGS.optimizer),
                      learning_rate=FLAGS.learning_rate)

    tb_writer = tf.summary.FileWriter(path.join(summaries_dir, 'train'), sess.graph)
    init_op = tf.global_variables_initializer()
    sess.run(init_op)

    saver = tf.train.Saver(max_to_keep=FLAGS.save_max_to_keep)
    save_path = path.join(ckpt_dir, FLAGS.model_name)

    latest_ckpt_step = 0

    # load checkpoint iff there exists correct checkpoint
    ckpt_cond1 = tf.train.checkpoint_exists(checkpoint_prefix=ckpt_dir)
    ckpt_cond2 = tf.train.latest_checkpoint(checkpoint_dir=ckpt_dir) is not None
    if ckpt_cond1 and ckpt_cond2:
        # in-line checkpoint restoration
        latest_path = tf.train.latest_checkpoint(checkpoint_dir=ckpt_dir)

        latest_ckpt_name = path.basename(latest_path)
        latest_ckpt_step = int(latest_ckpt_name.replace(FLAGS.model_name + '-', '')) + 1

        print('[I] {:.1f} : started restoring Model {:s} from {:s}'.format(time.time(), FLAGS.model_name, latest_ckpt_name))
        saver.restore(sess=sess, save_path=latest_path)

        print('[I] {:.1f} : Model {:s} restored from {:s} successfully!'.format(time.time(), FLAGS.model_name, latest_ckpt_name))
    else:
        print('[I] failed to load pre-trained model')

    # logger for validation error evaluation
    valid_error_log_path = path.join(ckpt_dir, "error_valid.log")
    valid_error_writer = open(valid_error_log_path, 'w')

    # logger for test error evaluation
    test_error_log_path = path.join(ckpt_dir, "error_test.log")
    test_error_writer = open(test_error_log_path, 'w')

    for epoch in range(FLAGS.epochs):

        # Perform training
        time_begin = time.time()
        print("[{:.1f}|epoch {:04d}] INIT".format(time_begin, epoch + 1))
        print("[{:.1f}|epoch {:04d}] TRAIN".format(time.time(), epoch + 1, ))

        # training
        train_errors = list()
        iters_train = dataset.get_iter_size(mode='train')
        for it in range(iters_train):
            scenes, tracks = dataset.get_next_batch(
                mode='train', out_as_np=True, skip_conf=True, scrap=2, start_idx=1)

            input_feed = {X: scenes, Y: tracks}
            output = sess.run(network.train(), feed_dict=input_feed)

            step_inception = (epoch * iters_train) + it
            if step_inception > 0 and step_inception % FLAGS.summary_step == 0:
                tb_writer.add_summary(output[-1], step_inception)
            else:
                pass

            print("[{:.1f}|epoch {:04d}|iter {:04d}]: RMSE {:.3f}".format(time.time(), epoch + 1, it + 1, output[1]))

            # append batch train errors to list
            train_errors.append(output[1])

        time_end = time.time()
        # reduce & mean train errors
        train_error_mean = np_mean(train_errors)
        print("[{:.1f}|epoch {:04d}] train error: {:.3f}".format(time_end, epoch + 1, train_error_mean))

        training_time = time_end - time_begin
        print("[{:.1f}|epoch {:04d}] Training elapsed time: {:.1f} s".format(time.time(), epoch+1, training_time))

        print("[epoch {0:04d}] VALID".format(epoch + 1))

        # validation
        valid_errors = list()
        iters_valid = dataset.get_iter_size(mode='valid')
        for it in range(iters_valid):
            scenes, tracks = dataset.get_next_batch(
                mode='valid', out_as_np=True, skip_conf=True, scrap=2, start_idx=1)

            input_feed = {X: scenes, Y: tracks}
            output = sess.run(network.valid(), feed_dict=input_feed)

            # append batch valid errors to list
            valid_errors.append(output)

        # reduce & mean valid errors
        valid_error_mean = np_mean(valid_errors)

        print("[epoch %04d] valid error: %.3f" % (epoch + 1, valid_error_mean))
        valid_error_writer.write("%.3f\n" % valid_error_mean)
        valid_error_writer.flush()

        print("[epoch {0:04d}] TEST".format(epoch + 1))

        # test
        test_errors = list()
        iters_test = dataset.get_iter_size(mode='test')
        for it in range(iters_test):
            scenes, tracks = dataset.get_next_batch(
                mode='test', out_as_np=True, skip_conf=True, scrap=2, start_idx=1)

            input_feed = {X: scenes, Y: tracks}
            output = sess.run(network.valid(), feed_dict=input_feed)

            # append batch test errors to list
            test_errors.append(output)

        # reduce & mean test errors
        test_error_mean = np_mean(test_errors)

        print("[epoch %04d] test error: %.3f" % (epoch + 1, test_error_mean))
        test_error_writer.write("%.3f\n" % test_error_mean)
        test_error_writer.flush()

        # save model per 1 epoch
        saved_path = saver.save(sess, save_path, global_step=epoch + latest_ckpt_step)
        print("Model %s-%d saved as %s" % (FLAGS.model_name, epoch + latest_ckpt_step, saved_path))

    # close valid/test_error_writer instance
    valid_error_writer.close()
    test_error_writer.close()

    # close tf session
    sess.close()


if __name__ == '__main__':
    # execute
    main()